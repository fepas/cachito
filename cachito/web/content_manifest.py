# SPDX-License-Identifier: GPL-3.0-or-later
import os
from copy import deepcopy
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, List, Optional

import flask

from cachito.common.utils import get_repo_name
from cachito.web.purl import (
    replace_parent_purl_placeholder,
    to_purl,
    to_top_level_purl,
    to_vcs_purl,
)
from cachito.web.utils import deep_sort_icm
from cachito.workers.pkg_managers import gomod

VERSION = 1
JSON_SCHEMA_URL = (
    "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/"
    "f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json"
)
SBOM_SCHEMA_URL = (
    "https://raw.githubusercontent.com/CycloneDX/specification/1.4/schema/bom-1.4.schema.json"
)
UNKNOWN_LAYER_INDEX = -1

# A base (empty) image content manifest which will be used as a template to
# fill in the generated image_contents.
# NOTE THAT, the image_contents must be filled in a copy of this base icm.
BASE_ICM = {
    "metadata": {
        "icm_version": VERSION,
        "icm_spec": JSON_SCHEMA_URL,
        "image_layer_index": UNKNOWN_LAYER_INDEX,
    },
    "image_contents": [],
}

BASE_SBOM = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.4",
    "version": 1,
    "components": [],
}


class ContentManifest:
    """A content manifest associated with a Cachito request."""

    def __init__(self, request, packages):
        """
        Initialize ContentManifest.

        :param Request request: the request to generate a ContentManifest for
        :param list[Package] packages: the packages contained in the request
        """
        self.request = request
        self.packages = packages
        # dict to store go package level data; uses the package id as key to identify a package
        self._gopkg_data = {}
        # dict to store go module level purl dependencies. Module names are used as keys
        self._gomod_data = {}
        # dict to store npm package data; uses the package id as key to identify a package
        self._npm_data = {}
        # dict to store pip package data; uses the package id as key to identify a package
        self._pip_data = {}
        # dict to store yarn package data; uses the package id as key to identify a package
        self._yarn_data = {}
        # dict to store rubygems package data; uses the package id as key to identify a package
        self._rubygems_data = {}
        # dict to store gitsubmodule package level data; uses the package id as key to identify a
        # package
        self._gitsubmodule_data = {}
        # list to store sbom components
        self._sbom_components = []

    @cached_property
    def go_modules_by_name(self) -> dict[str, "Package"]:
        """Get a mapping of go module names to their respective package object."""
        return {module.name: module for module in self.packages if module.type == "gomod"}

    def process_gomod(self, package, dependency, type="icm"):
        """
        Process gomod package.

        :param Package package: the gomod package to process
        :param Dependency dependency: the gomod package dependency to process
        :param type: icm or sbom component
        """
        if dependency.type == "gomod":

            parent_module_name = package.name
            relpath_from_parent_module_to_dep = None

            # if we're processing a local dependency
            if dependency.version and dependency.version.startswith("."):
                dep_normpath = os.path.normpath(os.path.join(package.name, dependency.version))
                parent_module_name = gomod.match_parent_module(
                    dep_normpath, self._gomod_data.keys()
                )
                # if the dependency is not in this repo, use a relative path from the root module
                # otherwise just use the parent purl directly
                if dependency.name not in self._gomod_data:
                    relpath_from_parent_module_to_dep = Path(dep_normpath).relative_to(
                        Path(parent_module_name)
                    )

            parent_purl = self._gomod_data[parent_module_name]["purl"]
            dep_purl = to_purl(dependency, relpath_from_parent_module_to_dep)
            dep_purl = replace_parent_purl_placeholder(dep_purl, parent_purl)
            icm_source = {"purl": dep_purl}

            if type == "icm":
                self._gomod_data[package.name]["dependencies"].append(icm_source)
            elif type == "sbom":
                component = {
                    "type": "library",
                    "name": dependency.name,
                    "purl": dep_purl,
                }
                if dependency.version:
                    component["version"] = dependency.version

                self._sbom_components.append(component)

    def process_go_package(self, package, dependency, type="icm"):
        """
        Process go-package package.

        :param Package package: the go-package package to process
        :param Dependency dependency: the go-package package dependency to process
        :param type: icm or sbom component
        """
        if dependency.type == "go-package":
            if dependency.version and dependency.version.startswith("."):
                dep_purl = self._get_local_go_package_dep_purl(package, dependency)
            else:
                dep_purl = to_purl(dependency)

            icm_dependency = {"purl": dep_purl}

            if type == "icm":
                self._gopkg_data[package]["dependencies"].append(icm_dependency)
            elif type == "sbom":
                component = {
                    "type": "library",
                    "name": dependency.name,
                    "purl": dep_purl,
                }
                if dependency.version:
                    component["version"] = dependency.version
                self._sbom_components.append(component)

    def _get_local_go_package_dep_purl(self, package: "Package", dependency: "Package") -> str:
        """
        Get the purl for a local go-package dependency.

        :param Package package: the go-package package to process
        :param Dependency dependency: the go-package package dependency to process
        """
        if not dependency.version or not dependency.version.startswith("."):
            raise ValueError(f"{dependency} has an invalid version for a local dependency")

        modules = self.go_modules_by_name
        dep_module_name = gomod.match_parent_module(dependency.name, modules.keys())

        # if the dep_module is in this repo, replace the dependency version with the module version
        if dep_module_name is not None:
            dependency.version = modules[dep_module_name].version
            return to_purl(dependency)

        # dep_module is not in this repo, so use a purl with a relative path from the root module
        package_module_name = gomod.match_parent_module(package.name, modules.keys())
        if package_module_name is None:
            # This should be impossible. A top-level go-package should match a module
            raise RuntimeError(f"Could not find parent Go module for package: {package.name}")

        dep_normpath = os.path.normpath(os.path.join(package_module_name, dependency.version))
        dep_module_name = gomod.match_parent_module(dep_normpath, modules.keys())
        if dep_module_name is None:
            # This should be impossible. The dep module should at least match the root module
            raise RuntimeError(f"Could not find parent Go module for package: {dep_normpath}")

        relpath_from_parent_module_to_dep = Path(dep_normpath).relative_to(Path(dep_module_name))
        parent_purl = self._gomod_data[dep_module_name]["purl"]
        dep_purl = to_purl(dependency, relpath_from_parent_module_to_dep)

        return replace_parent_purl_placeholder(dep_purl, parent_purl)

    def set_go_package_sources(self):
        """
        Adjust source level dependencies for go packages.

        Go packages are not related to Go modules in cachito's DB. However, Go
        sources are retreived in a per module basis. To set the proper source
        in each content manifest entry, we associate each Go package to a Go
        module based on their names.
        """
        for package_id, pkg_data in self._gopkg_data.items():
            pkg_name = pkg_data.pop("name")

            if pkg_name in self._gomod_data:
                module_name = pkg_name
            else:
                module_name = gomod.match_parent_module(pkg_name, self._gomod_data.keys())

            if module_name is not None:
                module = self._gomod_data[module_name]
                self._gopkg_data[package_id]["sources"] = module["dependencies"]
            else:
                flask.current_app.logger.warning(
                    "Could not find a Go module for %s", pkg_data["purl"]
                )

    def process_npm_package(self, package, dependency, type="icm"):
        """
        Process npm package.

        :param Package package: the npm package to process
        :param Dependency dependency: the npm package dependency to process
        :param type: icm or sbom component
        """
        if dependency.type == "npm":
            if type == "icm":
                self._process_standard_package("npm", package, dependency)
            elif type == "sbom":
                self._process_standard_package_sbom(dependency)

    def process_pip_package(self, package, dependency, type="icm"):
        """
        Process pip package.

        :param Package package: the pip package to process
        :param Dependency dependency: the pip package dependency to process
        :param type: icm or sbom component
        """
        if dependency.type == "pip":
            if type == "icm":
                self._process_standard_package("pip", package, dependency)
            elif type == "sbom":
                self._process_standard_package_sbom(dependency)

    def process_yarn_package(self, package, dependency, type="icm"):
        """
        Process yarn package.

        :param Package package: the yarn package to process
        :param Dependency dependency: the yarn package dependency to process
        :param type: icm or sbom component
        """
        if dependency.type == "yarn":
            if type == "icm":
                self._process_standard_package("yarn", package, dependency)
            elif type == "sbom":
                self._process_standard_package_sbom(dependency)

    def _process_standard_package(self, pkg_type, package, dependency):
        """
        Process a standard package (standard = does not require the same magic as go packages).

        Currently, all package types except for gomod and go-package are standard.
        """
        pkg_type_data = getattr(self, f"_{pkg_type}_data")

        icm_dependency = {"purl": to_purl(dependency)}
        pkg_type_data[package]["sources"].append(icm_dependency)
        if not dependency.dev:
            pkg_type_data[package]["dependencies"].append(icm_dependency)

    def _process_standard_package_sbom(self, dependency: "Package") -> None:
        """
        Process a standard package (standard = does not require the same magic as go packages).

        Currently, all package types except for gomod and go-package are standard.
        """
        purl = to_purl(dependency)
        component = {
            "type": "library",
            "name": dependency.name,
            "purl": purl,
        }
        if dependency.version:
            component["version"] = dependency.version
        self._sbom_components.append(component)

    def process_rubygems_package(self, package, dependency, type="icm"):
        """
        Process RubyGems package.

        :param Package package: the RubyGems package to process
        :param Dependency dependency: the RubyGems package dependency to process
        :param type: icm or sbom component
        """
        if dependency.type == "rubygems":
            parent_package_name = get_repo_name(self.request.repo).split("/")[-1]
            parent_purl = to_vcs_purl(parent_package_name, self.request.repo, self.request.ref)

            dep_purl = to_purl(dependency, package.path)
            dep_purl = replace_parent_purl_placeholder(dep_purl, parent_purl)

            icm_dependency = {"purl": dep_purl}

            if type == "icm":
                self._rubygems_data[package]["sources"].append(icm_dependency)
                self._rubygems_data[package]["dependencies"].append(icm_dependency)
            elif type == "sbom":
                component = {
                    "type": "library",
                    "name": dependency.name,
                    "purl": dep_purl,
                }
                if dependency.version:
                    component["version"] = dependency.version
                self._sbom_components.append(component)

    def to_json(self):
        """
        Generate the JSON representation of the content manifest.

        :return: the JSON form of the ContentManifest object
        :rtype: OrderedDict
        """
        self._gopkg_data = {}
        self._gomod_data = {}
        self._npm_data = {}
        self._pip_data = {}
        self._yarn_data = {}
        self._rubygems_data = {}
        self._gitsubmodule_data = {}

        for package in self.packages:

            if package.type == "go-package":
                purl = to_top_level_purl(package, self.request, subpath=package.path)
                self._gopkg_data.setdefault(
                    package,
                    {"name": package.name, "purl": purl, "dependencies": [], "sources": []},
                )
            elif package.type == "gomod":
                purl = to_top_level_purl(package, self.request, subpath=package.path)
                self._gomod_data.setdefault(package.name, {"purl": purl, "dependencies": []})
            elif package.type in ("npm", "pip", "yarn", "rubygems"):
                purl = to_top_level_purl(package, self.request, subpath=package.path)
                data = getattr(self, f"_{package.type}_data")
                data.setdefault(package, {"purl": purl, "dependencies": [], "sources": []})
            elif package.type == "git-submodule":
                purl = to_top_level_purl(package, self.request, subpath=package.path)
                self._gitsubmodule_data.setdefault(
                    package, {"purl": purl, "dependencies": [], "sources": []}
                )
            else:
                flask.current_app.logger.debug(
                    "No ICM implementation for '%s' packages", package.type
                )

        for package in self.packages:
            for dependency in package.dependencies:
                if package.type == "go-package":
                    self.process_go_package(package, dependency)
                elif package.type == "gomod":
                    self.process_gomod(package, dependency)
                elif package.type == "npm":
                    self.process_npm_package(package, dependency)
                elif package.type == "pip":
                    self.process_pip_package(package, dependency)
                elif package.type == "yarn":
                    self.process_yarn_package(package, dependency)
                elif package.type == "rubygems":
                    self.process_rubygems_package(package, dependency)

        # Adjust source level dependencies for go packages
        self.set_go_package_sources()

        top_level_packages = [
            *self._gopkg_data.values(),
            *self._npm_data.values(),
            *self._pip_data.values(),
            *self._yarn_data.values(),
            *self._rubygems_data.values(),
            *self._gitsubmodule_data.values(),
        ]
        return self.generate_icm(top_level_packages)

    def generate_icm(self, image_contents=None):
        """
        Generate a content manifest with the given image contents.

        :param list image_contents: List with components for the ICM's ``image_contents`` field
        :return: a valid Image Content Manifest
        :rtype: OrderedDict
        """
        icm = deepcopy(BASE_ICM)
        icm["image_contents"] = image_contents or []
        deep_sort_icm(icm)
        return icm

    def sbom_components_list(self) -> List[Dict[str, Any]]:
        """
        Generate sbom components list.

        :return: the CycloneDX components dict
        :rtype: list
        """
        self._sbom_components = []
        self._gomod_data = {}

        for package in self.packages:
            component = {"type": "library", "name": package.name}
            if package.version:
                component["version"] = package.version

            if package.type in (
                "go-package",
                "gomod",
                "npm",
                "pip",
                "yarn",
                "rubygems",
                "git-submodule",
            ):
                purl = to_top_level_purl(package, self.request, subpath=package.path)
                component["purl"] = purl

                if package.type == "gomod":
                    self._gomod_data.setdefault(package.name, {"purl": purl, "dependencies": []})

                self._sbom_components.append(component)

            else:
                flask.current_app.logger.debug(
                    "No SBOM implementation for '%s' packages", package.type
                )

        for package in self.packages:
            for dependency in package.dependencies:
                if package.type == "go-package":
                    self.process_go_package(package, dependency, type="sbom")
                elif package.type == "gomod":
                    self.process_gomod(package, dependency, type="sbom")
                elif package.type == "npm":
                    self.process_npm_package(package, dependency, type="sbom")
                elif package.type == "pip":
                    self.process_pip_package(package, dependency, type="sbom")
                elif package.type == "yarn":
                    self.process_yarn_package(package, dependency, type="sbom")
                elif package.type == "rubygems":
                    self.process_rubygems_package(package, dependency, type="sbom")

        return self._sbom_components


class Package:
    """
    A package within a content manifest.

    It is used primarily to generate a package URL (purl).
    """

    __slots__ = ("name", "type", "version", "dev", "dependencies", "path")

    def __init__(
        self,
        name: str,
        type: str,
        version: Optional[str] = None,
        dev: bool = False,
        path: Optional[str] = None,
        dependencies: Optional[List] = None,
    ):
        """Initialize package data."""
        self.name = name
        self.type = type
        self.version = version
        self.dev = dev
        self.dependencies = [] if dependencies is None else dependencies
        self.path = path

    def __repr__(self):
        return (
            f"<{self.__class__.__name__} name={self.name}, type={self.type}, "
            f"version={self.version}>"
        )

    def __hash__(self):
        return hash((self.name, self.type, self.version, self.dev))

    def __eq__(self, other):
        return (
            isinstance(other, Package)
            and self.name == other.name
            and self.type == other.type
            and self.version == other.version
            and self.dev == other.dev
        )

    @classmethod
    def from_json(cls, package):
        """
        Create a Package object from JSON.

        All dependencies will also be converted to Package objects.

        :param dict package: the dictionary representing the package
        :return: the Package object
        :rtype: Package
        """
        dependencies = [
            Package.from_json(dependency) for dependency in package.get("dependencies", [])
        ]

        return cls(
            name=package["name"],
            type=package["type"],
            version=package["version"],
            dev=package.get("dev", False),
            dependencies=dependencies,
            path=package.get("path"),
        )
