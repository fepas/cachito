# SPDX-License-Identifier: GPL-3.0-or-later
from collections import OrderedDict
from typing import Any, Dict, List
from unittest import mock

import pytest

from cachito.errors import ContentManifestError
from cachito.web.content_manifest import JSON_SCHEMA_URL, ContentManifest, Package
from cachito.web.models import Request
from cachito.web.purl import PARENT_PURL_PLACEHOLDER, to_purl, to_top_level_purl, to_vcs_purl

GIT_REPO = "https://github.com/namespace/repo"
GIT_REF = "1798a59f297f5f3886e41bc054e538540581f8ce"

DEFAULT_PURL = f"pkg:github/namespace/repo@{GIT_REF}"
DEP_COMMIT_ID = "58c88e4952e95935c0dd72d4a24b0c44f2249f5b"


@pytest.fixture
def default_request():
    """Get default request to use in tests."""
    return Request(repo=GIT_REPO, ref=GIT_REF)


@pytest.fixture
def default_toplevel_purl():
    """Get VCS purl for default request."""
    return DEFAULT_PURL


def _load_packages_from_json(packages_json):
    return [Package.from_json(package) for package in packages_json]


def test_process_go(app, default_request):
    packages_json = [
        {
            "name": "example.com/org/project",
            "type": "go-package",
            "version": "1.1.1",
            "dependencies": [
                {"name": "example.com/org/project/lib", "type": "go-package", "version": "2.2.2"}
            ],
        },
        {
            "name": "example.com/org/project",
            "type": "gomod",
            "version": "1.1.1",
            "dependencies": [
                {"name": "example.com/anotherorg/project/lib", "type": "gomod", "version": "3.3.3"}
            ],
        },
    ]

    expected_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1"
    expected_dep_purl = "pkg:golang/example.com%2Forg%2Fproject%2Flib@2.2.2"
    expected_src_purl = "pkg:golang/example.com%2Fanotherorg%2Fproject%2Flib@3.3.3"

    packages = _load_packages_from_json(packages_json)
    package = packages[0]

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_gopkg_contents = {
        package: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_src_purl}],
        },
    }

    expected_gomod_contents = {
        package.name: {"purl": expected_purl, "dependencies": [{"purl": expected_src_purl}]}
    }

    assert cm._gopkg_data
    assert package in cm._gopkg_data
    assert package.name in cm._gomod_data
    assert cm._gopkg_data == expected_gopkg_contents
    assert cm._gomod_data == expected_gomod_contents


def test_process_gomod_with_local_dep(default_request: Request):
    packages_json = [
        {
            "name": "example.com/org/project",
            "type": "gomod",
            "version": "1.1.1",
            "dependencies": [],
        },
        {
            "name": "example.com/org/project/foo",
            "type": "gomod",
            "version": "1.1.1",
            "dependencies": [
                {"name": "example.com/org/project", "type": "gomod", "version": "../"}
            ],
        },
    ]

    expected_parent_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1"
    expected_foo_purl = "pkg:golang/example.com%2Forg%2Fproject%2Ffoo@1.1.1"
    expected_src_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1"

    packages = _load_packages_from_json(packages_json)
    parent_module = packages[0]
    foo_module = packages[1]

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_gomod_contents = {
        parent_module.name: {"purl": expected_parent_purl, "dependencies": []},
        foo_module.name: {"purl": expected_foo_purl, "dependencies": [{"purl": expected_src_purl}]},
    }

    assert cm._gomod_data == expected_gomod_contents


def test_process_gomod_with_local_dep_unprocessed_module(default_request: Request):
    packages_json = [
        {
            "name": "example.com/org/project",
            "type": "gomod",
            "version": "1.1.1",
            "dependencies": [
                {
                    "name": "example.com/anotherorg/project",
                    "type": "gomod",
                    "version": "./staging/src/anotherorg/project",
                },
            ],
        },
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[0]

    expected_module_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1"
    expected_dependency_purl = f"{expected_module_purl}#staging/src/anotherorg/project"

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    assert cm._gomod_data == {
        package.name: {
            "purl": expected_module_purl,
            "dependencies": [{"purl": expected_dependency_purl}],
        },
    }


def test_process_go_package_with_local_dep_unprocessed_module(default_request: Request):
    packages_json = [
        {
            "name": "example.com/org/project",
            "type": "gomod",
            "version": "1.1.1",
            "dependencies": [
                {
                    "name": "example.com/otherorg/project/foo",
                    "type": "gomod",
                    "version": "./foo",
                },
            ],
        },
        {
            "name": "example.com/org/project",
            "type": "go-package",
            "version": "1.1.1",
            "dependencies": [
                {
                    "name": "example.com/otherorg/project/foo/package",
                    "type": "go-package",
                    "version": "./foo/package",
                },
            ],
        },
    ]

    root_module_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1"
    foo_module_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1#foo"
    foo_package_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1#foo/package"

    packages = _load_packages_from_json(packages_json)
    root_module = packages[0]
    root_package = packages[1]

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_gopkg_contents = {
        root_package: {
            "purl": root_module_purl,
            "dependencies": [{"purl": foo_package_purl}],
            "sources": [{"purl": foo_module_purl}],
        },
    }

    expected_gomod_contents = {
        root_module.name: {"purl": root_module_purl, "dependencies": [{"purl": foo_module_purl}]},
    }

    assert cm._gopkg_data == expected_gopkg_contents
    assert cm._gomod_data == expected_gomod_contents


def test_process_go_package_with_local_dep(default_request: Request):
    packages_json = [
        {
            "name": "example.com/org/project",
            "type": "gomod",
            "version": "1.1.1",
            "dependencies": [
                {
                    "name": "example.com/org/project/foo",
                    "type": "gomod",
                    "version": "./foo",
                },
            ],
        },
        {
            "name": "example.com/org/project/foo",
            "type": "gomod",
            "version": "2.2.2",
            "dependencies": [],
        },
        {
            "name": "example.com/org/project",
            "type": "go-package",
            "version": "1.1.1",
            "dependencies": [
                {
                    "name": "example.com/org/project/foo/package",
                    "type": "go-package",
                    "version": "./foo/package",
                },
            ],
        },
    ]

    root_module_purl = "pkg:golang/example.com%2Forg%2Fproject@1.1.1"
    foo_module_purl = "pkg:golang/example.com%2Forg%2Fproject%2Ffoo@2.2.2"
    foo_package_purl = "pkg:golang/example.com%2Forg%2Fproject%2Ffoo%2Fpackage@2.2.2"

    packages = _load_packages_from_json(packages_json)
    root_module = packages[0]
    foo_module = packages[1]
    root_package = packages[2]

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_gopkg_contents = {
        root_package: {
            "purl": root_module_purl,
            "dependencies": [{"purl": foo_package_purl}],
            "sources": [{"purl": foo_module_purl}],
        },
    }

    expected_gomod_contents = {
        root_module.name: {"purl": root_module_purl, "dependencies": [{"purl": foo_module_purl}]},
        foo_module.name: {"purl": foo_module_purl, "dependencies": []},
    }

    assert cm._gopkg_data == expected_gopkg_contents
    assert cm._gomod_data == expected_gomod_contents


def test_process_npm(default_request, default_toplevel_purl):
    dep_commit_id = "7762177aacfb1ddf5ca45cebfe8de1da3b24f0ff"

    packages_json = [
        {
            "name": "grc-ui",
            "type": "npm",
            "version": "1.0.0",
            "dependencies": [
                {
                    "name": "security-middleware",
                    "type": "npm",
                    "version": f"github:open-cluster-management/security-middleware"
                    f"#{dep_commit_id}",
                },
                {"name": "@types/events", "type": "npm", "version": "3.0.0", "dev": True},
            ],
        },
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[0]

    expected_purl = default_toplevel_purl
    expected_dep_purl = f"pkg:github/open-cluster-management/security-middleware@{dep_commit_id}"
    expected_src_purl = "pkg:npm/%40types/events@3.0.0"

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_contents = {
        package: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_dep_purl}, {"purl": expected_src_purl}],
        },
    }

    assert cm._npm_data
    assert package in cm._npm_data
    assert cm._npm_data == expected_contents


def test_process_yarn(default_request, default_toplevel_purl):
    packages_json = [
        {
            "name": "grc-ui",
            "type": "yarn",
            "version": "1.0.0",
            "dependencies": [
                {
                    "name": "security-middleware",
                    "type": "yarn",
                    "version": f"github:open-cluster-management/security-middleware"
                    f"#{DEP_COMMIT_ID}",
                },
                {"name": "@types/events", "type": "yarn", "version": "3.0.0", "dev": True},
            ],
        },
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[0]

    expected_purl = default_toplevel_purl
    expected_dep_purl = f"pkg:github/open-cluster-management/security-middleware@{DEP_COMMIT_ID}"
    expected_src_purl = "pkg:npm/%40types/events@3.0.0"

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_contents = {
        package: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_dep_purl}, {"purl": expected_src_purl}],
        },
    }

    assert cm._yarn_data
    assert package in cm._yarn_data
    assert cm._yarn_data == expected_contents


def test_process_pip(default_request, default_toplevel_purl):
    packages_json = [
        {
            "name": "requests",
            "type": "pip",
            "version": "2.24.0",
            "dependencies": [
                {
                    "name": "cnr-server",
                    "type": "pip",
                    "version": f"git+https://github.com/quay/appr@{DEP_COMMIT_ID}",
                },
                {"name": "setuptools", "type": "pip", "version": "49.1.1", "dev": True},
            ],
        },
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[0]

    expected_purl = default_toplevel_purl
    expected_dep_purl = f"pkg:github/quay/appr@{DEP_COMMIT_ID}"
    expected_src_purl = "pkg:pypi/setuptools@49.1.1"

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_contents = {
        package: {
            "purl": expected_purl,
            "dependencies": [{"purl": expected_dep_purl}],
            "sources": [{"purl": expected_dep_purl}, {"purl": expected_src_purl}],
        },
    }

    assert cm._pip_data
    assert package in cm._pip_data
    assert cm._pip_data == expected_contents


def test_process_rubygems(default_request, default_toplevel_purl):
    packages_json = [
        {
            "name": "zync",
            "type": "rubygems",
            "version": "2.24.0",
            "dependencies": [
                {
                    "name": "httpclient",
                    "type": "rubygems",
                    "version": f"git+https://github.com/3scale/httpclient.git@{DEP_COMMIT_ID}",
                    "path": "some/path",
                },
                {"name": "zeitwerk", "type": "rubygems", "version": "2.4.2"},
                {
                    "name": "active-docs",
                    "type": "rubygems",
                    "version": "./vendor/active-docs",
                },
            ],
        },
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[0]

    expected_purl = default_toplevel_purl
    expected_git_purl = f"pkg:github/3scale/httpclient@{DEP_COMMIT_ID}"
    expected_gem_purl = "pkg:gem/zeitwerk@2.4.2"
    expected_path_purl = f"{expected_purl}#vendor/active-docs"

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_contents = {
        package: {
            "purl": expected_purl,
            "dependencies": [
                {"purl": expected_gem_purl},
                {"purl": expected_git_purl},
                {"purl": expected_path_purl},
            ],
            "sources": [
                {"purl": expected_gem_purl},
                {"purl": expected_git_purl},
                {"purl": expected_path_purl},
            ],
        },
    }

    assert cm._rubygems_data
    assert package in cm._rubygems_data
    assert cm._rubygems_data == expected_contents


def test_process_manifest_subpath(default_request, default_toplevel_purl):
    packages_json = [
        {
            "name": "cachito-rubygems-multiple/first_pkg",
            "type": "rubygems",
            "version": "d5f91c54a8b35c3f2bdcf9a602184022b003ed75",
            "dependencies": [
                {
                    "name": "pathgem",
                    "version": "./vendor/pathgem",
                    "type": "rubygems",
                }
            ],
            "path": "first_pkg",
        }
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[0]

    expected_purl = f"{default_toplevel_purl}#first_pkg"
    expected_dependency = f"{default_toplevel_purl}#first_pkg/vendor/pathgem"

    cm = ContentManifest(default_request, packages)
    cm.to_json()

    expected_contents = {
        package: {
            "purl": expected_purl,
            "dependencies": [
                {"purl": expected_dependency},
            ],
            "sources": [
                {"purl": expected_dependency},
            ],
        },
    }

    assert cm._rubygems_data
    assert package in cm._rubygems_data
    assert cm._rubygems_data == expected_contents


@pytest.mark.parametrize(
    "package_json",
    [
        {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0"},
        {
            "name": "example.com/org/project",
            "type": "gomod",
            "version": "1.0.0",
            "dev": True,
            "path": "folder",
        },
        {
            "name": "example.com/org/project",
            "type": "gomod",
            "version": "1.0.0",
            "invalid-attribute": "some-value",
        },
        {
            "name": "example.com/org/project",
            "type": "gomod",
            "version": "1.0.0",
            "dependencies": [
                {"name": "example.com/org/project/dep", "type": "gomod", "version": "1.0.0"},
            ],
        },
    ],
)
def test_package_from_json(package_json):
    package = Package.from_json(package_json)

    assert package.name == package_json.get("name")
    assert package.type == package_json.get("type")
    assert package.version == package_json.get("version")
    assert package.dev == package_json.get("dev", False)
    assert package.path == package_json.get("path")

    if "dependencies" in package_json:
        dependency = package.dependencies[0]
        dependency_json = package_json["dependencies"][0]

        assert type(dependency) == Package
        assert dependency.name == dependency_json["name"]
        assert dependency.type == dependency_json["type"]
        assert dependency.version == dependency_json["version"]


@pytest.mark.parametrize(
    "json1, json2, equality",
    [
        (
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0"},
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0"},
            True,
        ),
        (
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0", "dev": True},
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0", "dev": True},
            True,
        ),
        (
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0", "dev": True},
            {"name": "example.com/org/project1", "type": "gomod", "version": "1.0.0", "dev": True},
            False,
        ),
        (
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0", "dev": True},
            {"name": "example.com/org/project", "type": "npm", "version": "1.0.0", "dev": True},
            False,
        ),
        (
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0", "dev": True},
            {"name": "example.com/org/project", "type": "gomod", "version": "2.0.0", "dev": True},
            False,
        ),
        (
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0", "dev": True},
            {"name": "example.com/org/project", "type": "gomod", "version": "1.0.0", "dev": False},
            False,
        ),
    ],
)
def test_package_equality(json1, json2, equality):
    package1 = Package.from_json(json1)
    package2 = Package.from_json(json2)

    assert (package1 == package2) == equality


@pytest.mark.parametrize(
    "package",
    [
        None,
        {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"},
        {"name": "grc-ui", "type": "npm", "version": "1.0.0"},
        {"name": "requests", "type": "pip", "version": "2.24.0"},
        {"name": "grc-ui", "type": "yarn", "version": "1.0.0"},
    ],
)
@pytest.mark.parametrize("subpath", [None, "some/path"])
@mock.patch("cachito.web.content_manifest.to_top_level_purl")
def test_to_json(mock_top_level_purl, app, package, subpath):
    request = Request()

    if package and subpath:
        package["path"] = subpath

    packages = _load_packages_from_json([package]) if package else []

    cm = ContentManifest(request, packages)
    image_contents = []

    if package:
        content = {"purl": mock_top_level_purl.return_value, "dependencies": [], "sources": []}
        image_contents.append(content)

    expected = {
        "metadata": {"icm_version": 1, "icm_spec": JSON_SCHEMA_URL, "image_layer_index": -1},
        "image_contents": image_contents,
    }
    assert cm.to_json() == expected

    if package:
        package = Package.from_json(package)
        mock_top_level_purl.assert_called_once_with(package, request, subpath=subpath)


@pytest.mark.parametrize(
    "package, components",
    [
        [
            None,
            [],
        ],
        [
            {
                "name": "example.com/org/project",
                "type": "go-package",
                "version": "1.1.1",
                "dependencies": [
                    {
                        "name": "example.com/org/project_dep",
                        "type": "go-package",
                        "version": "1.1.1_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "example.com/org/project",
                    "version": "1.1.1",
                    "purl": "pkg:golang/example.com%2Forg%2Fproject@1.1.1",
                },
                {
                    "type": "library",
                    "name": "example.com/org/project_dep",
                    "version": "1.1.1_dep",
                    "purl": "pkg:golang/example.com%2Forg%2Fproject_dep@1.1.1_dep",
                },
            ],
        ],
        [
            {
                "name": "example.com/org/project",
                "type": "go-package",
                "version": "1.1.1",
                "path": "some/path",
                "dependencies": [
                    {
                        "name": "example.com/org/project_dep",
                        "type": "go-package",
                        "version": "1.1.1_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "example.com/org/project",
                    "version": "1.1.1",
                    "purl": "pkg:golang/example.com%2Forg%2Fproject@1.1.1",
                },
                {
                    "type": "library",
                    "name": "example.com/org/project_dep",
                    "version": "1.1.1_dep",
                    "purl": "pkg:golang/example.com%2Forg%2Fproject_dep@1.1.1_dep",
                },
            ],
        ],
        [
            {
                "name": "gomod-some",
                "type": "gomod",
                "version": "1.0.0",
                "dependencies": [
                    {
                        "name": "gomod-some_dep",
                        "type": "gomod",
                        "version": "1.0.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "gomod-some",
                    "version": "1.0.0",
                    "purl": "pkg:golang/gomod-some@1.0.0",
                },
                {
                    "type": "library",
                    "name": "gomod-some_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:golang/gomod-some_dep@1.0.0_dep",
                },
            ],
        ],
        [
            {
                "name": "gomod-some",
                "type": "gomod",
                "version": "1.0.0",
                "path": "some/path",
                "dependencies": [
                    {
                        "name": "gomod-some_dep",
                        "type": "gomod",
                        "version": "1.0.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "gomod-some",
                    "version": "1.0.0",
                    "purl": "pkg:golang/gomod-some@1.0.0",
                },
                {
                    "type": "library",
                    "name": "gomod-some_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:golang/gomod-some_dep@1.0.0_dep",
                },
            ],
        ],
        [
            {
                "name": "grc-ui",
                "type": "npm",
                "version": "1.0.0",
                "dependencies": [
                    {
                        "name": "grc-ui_dep",
                        "type": "npm",
                        "version": "1.0.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "grc-ui",
                    "version": "1.0.0",
                    "purl": DEFAULT_PURL,
                },
                {
                    "type": "library",
                    "name": "grc-ui_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:npm/grc-ui_dep@1.0.0_dep",
                },
            ],
        ],
        [
            {
                "name": "grc-ui",
                "type": "npm",
                "version": "1.0.0",
                "path": "some/path",
                "dependencies": [
                    {
                        "name": "grc-ui_dep",
                        "type": "npm",
                        "version": "1.0.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "grc-ui",
                    "version": "1.0.0",
                    "purl": f"{DEFAULT_PURL}#some/path",
                },
                {
                    "type": "library",
                    "name": "grc-ui_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:npm/grc-ui_dep@1.0.0_dep",
                },
            ],
        ],
        [
            {
                "name": "requests",
                "type": "pip",
                "version": "2.24.0",
                "dependencies": [
                    {
                        "name": "requests_dep",
                        "type": "pip",
                        "version": "2.24.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "requests",
                    "version": "2.24.0",
                    "purl": DEFAULT_PURL,
                },
                {
                    "type": "library",
                    "name": "requests_dep",
                    "version": "2.24.0_dep",
                    "purl": "pkg:pypi/requests-dep@2.24.0_dep",
                },
            ],
        ],
        [
            {
                "name": "requests",
                "type": "pip",
                "version": "2.24.0",
                "path": "some/path",
                "dependencies": [
                    {
                        "name": "requests_dep",
                        "type": "pip",
                        "version": "2.24.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "requests",
                    "version": "2.24.0",
                    "purl": f"{DEFAULT_PURL}#some/path",
                },
                {
                    "type": "library",
                    "name": "requests_dep",
                    "version": "2.24.0_dep",
                    "purl": "pkg:pypi/requests-dep@2.24.0_dep",
                },
            ],
        ],
        [
            {
                "name": "grc-ui",
                "type": "yarn",
                "version": "1.0.0",
                "dependencies": [
                    {
                        "name": "grc-ui_dep",
                        "type": "yarn",
                        "version": "1.0.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "grc-ui",
                    "version": "1.0.0",
                    "purl": DEFAULT_PURL,
                },
                {
                    "type": "library",
                    "name": "grc-ui_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:npm/grc-ui_dep@1.0.0_dep",
                },
            ],
        ],
        [
            {
                "name": "grc-ui",
                "type": "yarn",
                "version": "1.0.0",
                "path": "some/path",
                "dependencies": [
                    {
                        "name": "grc-ui_dep",
                        "type": "yarn",
                        "version": "1.0.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "grc-ui",
                    "version": "1.0.0",
                    "purl": f"{DEFAULT_PURL}#some/path",
                },
                {
                    "type": "library",
                    "name": "grc-ui_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:npm/grc-ui_dep@1.0.0_dep",
                },
            ],
        ],
        [
            {
                "name": "ruby-some",
                "type": "rubygems",
                "version": "1.0.0",
                "dependencies": [
                    {
                        "name": "ruby-some_dep",
                        "type": "rubygems",
                        "version": "1.0.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "ruby-some",
                    "version": "1.0.0",
                    "purl": DEFAULT_PURL,
                },
                {
                    "type": "library",
                    "name": "ruby-some_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:gem/ruby-some_dep@1.0.0_dep",
                },
            ],
        ],
        [
            {
                "name": "ruby-some",
                "type": "rubygems",
                "version": "1.0.0",
                "path": "some/path",
                "dependencies": [
                    {
                        "name": "ruby-some_dep",
                        "type": "rubygems",
                        "version": "1.0.0_dep",
                    },
                ],
            },
            [
                {
                    "type": "library",
                    "name": "ruby-some",
                    "version": "1.0.0",
                    "purl": f"{DEFAULT_PURL}#some/path",
                },
                {
                    "type": "library",
                    "name": "ruby-some_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:gem/ruby-some_dep@1.0.0_dep",
                },
            ],
        ],
        [
            {
                "name": "submodule-some",
                "type": "git-submodule",
                "version": "example.com/some.git#58c88e4952e95935c0dd72d4a24b0c44f2249f5b",
            },
            [
                {
                    "type": "library",
                    "name": "submodule-some",
                    "version": "example.com/some.git#58c88e4952e95935c0dd72d4a24b0c44f2249f5b",
                    "purl": "pkg:generic/submodule-some?vcs_url=example.com%2Fsome.git%"
                    "4058c88e4952e95935c0dd72d4a24b0c44f2249f5b",
                },
            ],
        ],
        [
            {
                "name": "submodule-some",
                "type": "git-submodule",
                "path": "some/path",
                "version": "example.com/some.git#58c88e4952e95935c0dd72d4a24b0c44f2249f5b",
            },
            [
                {
                    "type": "library",
                    "name": "submodule-some",
                    "version": "example.com/some.git#58c88e4952e95935c0dd72d4a24b0c44f2249f5b",
                    "purl": "pkg:generic/submodule-some?vcs_url=example.com%2Fsome.git%"
                    "4058c88e4952e95935c0dd72d4a24b0c44f2249f5b",
                },
            ],
        ],
    ],
)
def test_sbom_components_one_package(
    package: Dict[str, Any], components: List[Dict[str, Any]], default_request: Request
) -> None:
    packages = _load_packages_from_json([package]) if package else []

    cm = ContentManifest(default_request, packages)

    assert components == cm.sbom_components_list()


@pytest.mark.parametrize(
    "packages_json, components",
    [
        [
            [
                {
                    "name": "example.com/org/project",
                    "type": "go-package",
                    "version": "1.1.1",
                    "dependencies": [
                        {
                            "name": "example.com/org/project_dep",
                            "type": "go-package",
                            "version": "1.1.1_dep",
                        },
                    ],
                },
                {
                    "name": "grc-ui",
                    "type": "npm",
                    "version": "1.0.0",
                    "dependencies": [
                        {
                            "name": "grc-ui_dep",
                            "type": "npm",
                            "version": "1.0.0_dep",
                        },
                    ],
                },
                {
                    "name": "requests",
                    "type": "pip",
                    "version": "2.24.0",
                    "dependencies": [
                        {
                            "name": "requests_dep",
                            "type": "pip",
                            "version": "2.24.0_dep",
                        },
                    ],
                },
                {
                    "name": "grc-ui2",
                    "type": "yarn",
                    "version": "1.0.0",
                    "dependencies": [
                        {
                            "name": "grc-ui2_dep",
                            "type": "yarn",
                            "version": "1.0.0_dep",
                        },
                    ],
                },
                {
                    "name": "gomod-some",
                    "type": "gomod",
                    "version": "1.0.0",
                    "dependencies": [
                        {
                            "name": "gomod-some_dep",
                            "type": "gomod",
                            "version": "1.0.0_dep",
                        },
                    ],
                },
            ],
            [
                {
                    "type": "library",
                    "name": "example.com/org/project",
                    "version": "1.1.1",
                    "purl": "pkg:golang/example.com%2Forg%2Fproject@1.1.1",
                },
                {
                    "type": "library",
                    "name": "grc-ui",
                    "version": "1.0.0",
                    "purl": DEFAULT_PURL,
                },
                {
                    "type": "library",
                    "name": "requests",
                    "version": "2.24.0",
                    "purl": DEFAULT_PURL,
                },
                {
                    "type": "library",
                    "name": "grc-ui2",
                    "version": "1.0.0",
                    "purl": DEFAULT_PURL,
                },
                {
                    "type": "library",
                    "name": "gomod-some",
                    "version": "1.0.0",
                    "purl": "pkg:golang/gomod-some@1.0.0",
                },
                {
                    "type": "library",
                    "name": "example.com/org/project_dep",
                    "version": "1.1.1_dep",
                    "purl": "pkg:golang/example.com%2Forg%2Fproject_dep@1.1.1_dep",
                },
                {
                    "type": "library",
                    "name": "grc-ui_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:npm/grc-ui_dep@1.0.0_dep",
                },
                {
                    "type": "library",
                    "name": "requests_dep",
                    "version": "2.24.0_dep",
                    "purl": "pkg:pypi/requests-dep@2.24.0_dep",
                },
                {
                    "type": "library",
                    "name": "grc-ui2_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:npm/grc-ui2_dep@1.0.0_dep",
                },
                {
                    "type": "library",
                    "name": "gomod-some_dep",
                    "version": "1.0.0_dep",
                    "purl": "pkg:golang/gomod-some_dep@1.0.0_dep",
                },
            ],
        ],
    ],
)
def test_sbom_components_multiple_packages(
    packages_json: List[Dict[str, Any]], components: List[Dict[str, Any]], default_request: Request
) -> None:
    packages = _load_packages_from_json(packages_json)

    cm = ContentManifest(default_request, packages)

    assert components == cm.sbom_components_list()


@pytest.mark.parametrize(
    "packages_json",
    [
        [
            {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"},
            {
                "name": "tour",
                "type": "git-submodule",
                "version": (
                    "https://github.com/testrepo/tour.git#58c88e4952e95935c0dd72d4a24b0c44f2249f5b"
                ),
            },
        ],
    ],
)
@mock.patch("cachito.web.content_manifest.ContentManifest.generate_icm")
def test_to_json_with_multiple_packages(mock_generate_icm, app, packages_json):
    request = Request()
    packages = _load_packages_from_json(packages_json)
    cm = ContentManifest(request, packages)
    image_contents = []

    for package_json in packages_json:
        package = Package.from_json(package_json)
        content = {"purl": to_purl(package), "dependencies": [], "sources": []}
        image_contents.append(content)

    res = cm.to_json()

    mock_generate_icm.assert_called_once_with(image_contents)
    assert res == mock_generate_icm.return_value


@pytest.mark.parametrize("contents", [None, [], "foobar", 42, OrderedDict({"egg": "bacon"})])
def test_generate_icm(contents, default_request):
    cm = ContentManifest(default_request, [])
    expected = OrderedDict(
        {
            "image_contents": contents or [],
            "metadata": OrderedDict(
                {"icm_spec": JSON_SCHEMA_URL, "icm_version": 1, "image_layer_index": -1}
            ),
        }
    )
    assert cm.generate_icm(contents) == expected


@pytest.mark.parametrize(
    "pkg_name, gomod_data, warn",
    [
        ["example.com/foo/bar", {}, True],
        [
            "example.com/foo/bar",
            {"example.com/foo/bar": {"purl": "not-important", "dependencies": []}},
            False,
        ],
        [
            "example.com/foo/bar",
            {"example.com/foo/bar": {"purl": "not-important", "dependencies": [{"purl": "foo"}]}},
            False,
        ],
        [
            "example.com/foo/bar",
            {"example.com/foo": {"purl": "not-important", "dependencies": [{"purl": "foo"}]}},
            False,
        ],
        [
            "example.com/foo",
            {"example.com/foo/bar": {"purl": "not-important", "dependencies": [{"purl": "foo"}]}},
            True,
        ],
    ],
)
@mock.patch("flask.current_app.logger.warning")
def test_set_go_package_sources(mock_warning, app, pkg_name, gomod_data, warn, default_request):
    cm = ContentManifest(default_request, [])

    main_purl = "pkg:golang/a-package"
    main_package_id = 1

    cm._gopkg_data = {
        main_package_id: {"name": pkg_name, "purl": main_purl, "sources": [], "dependencies": []}
    }
    cm._gomod_data = gomod_data
    cm.set_go_package_sources()

    sources = []
    for v in gomod_data.values():
        if any(k in pkg_name for k in gomod_data.keys()):
            sources += v["dependencies"]

    expected = {main_package_id: {"purl": main_purl, "dependencies": [], "sources": sources}}

    assert cm._gopkg_data == expected

    if warn:
        mock_warning.assert_called_once_with("Could not find a Go module for %s", main_purl)
    else:
        mock_warning.assert_not_called()


@pytest.mark.parametrize(
    "package, expected_purl, parent_package_rel_path, defined, known_protocol",
    [
        [{"name": "bacon", "type": "invalid", "version": "1.0.0"}, None, None, False, False],
        [
            {"name": "example.com/org/project", "type": "go-package", "version": "1.1.1"},
            "pkg:golang/example.com%2Forg%2Fproject@1.1.1",
            None,
            True,
            True,
        ],
        [
            {"name": "example.com/org/project", "type": "gomod", "version": "1.1.1"},
            "pkg:golang/example.com%2Forg%2Fproject@1.1.1",
            None,
            True,
            True,
        ],
        [
            {"name": "example.com/org/project", "type": "go-package", "version": "./src/project"},
            f"{PARENT_PURL_PLACEHOLDER}#src/project",
            "src/project",
            True,
            True,
        ],
        [{"name": "fmt", "type": "go-package", "version": ""}, "pkg:golang/fmt", None, True, True],
        [
            {"name": "example.com/org/project", "type": "gomod", "version": "./src/project"},
            f"{PARENT_PURL_PLACEHOLDER}#src/project",
            "src/project",
            True,
            True,
        ],
        [
            {"name": "example.com/org/project", "type": "gomod", "version": "./src/project"},
            f"{PARENT_PURL_PLACEHOLDER}",
            None,
            True,
            True,
        ],
        [
            {"name": "grc-ui", "type": "npm", "version": "1.0.0"},
            "pkg:npm/grc-ui@1.0.0",
            None,
            True,
            True,
        ],
        [
            {
                "name": "security-middleware",
                "type": "npm",
                "version": "github:open-cluster-management/security-middleware#i0am0a0commit0hash",
            },
            "pkg:github/open-cluster-management/security-middleware@i0am0a0commit0hash",
            None,
            True,
            True,
        ],
        [
            {
                "name": "security-middleware",
                "type": "npm",
                "version": "gitlab:deep/nested/repo/security-middleware#i0am0a0commit0hash",
            },
            "pkg:gitlab/deep/nested/repo/security-middleware@i0am0a0commit0hash",
            None,
            True,
            True,
        ],
        [
            {
                "name": "fromgit",
                "type": "npm",
                "version": "git://some.domain/my/project/repo.git#i0am0a0commit0hash",
            },
            (
                "pkg:generic/fromgit?vcs_url=git%3A%2F%2Fsome.domain%2Fmy%2Fproject%2Frepo.git"
                "%23i0am0a0commit0hash"
            ),
            None,
            True,
            True,
        ],
        [
            {
                "name": "fromweb",
                "type": "npm",
                "version": "https://some.domain/my/project/package.tar.gz",
            },
            (
                "pkg:generic/fromweb?download_url=https%3A%2F%2Fsome.domain%2Fmy%2Fproject"
                "%2Fpackage.tar.gz"
            ),
            None,
            True,
            True,
        ],
        [
            {"name": "fromfile", "type": "npm", "version": "file:client-default"},
            "generic/fromfile?file%3Aclient-default",
            None,
            True,
            True,
        ],
        [
            {
                "name": "fromunknown",
                "type": "npm",
                "version": "unknown://some.domain/my/project/package.tar.gz",
            },
            None,
            None,
            True,
            False,
        ],
        [
            {"name": "requests", "type": "pip", "version": "2.24.0"},
            "pkg:pypi/requests@2.24.0",
            None,
            True,
            True,
        ],
        [
            {"name": "requests_FOO bar", "type": "pip", "version": "2.24.0"},
            "pkg:pypi/requests-foo-bar@2.24.0",
            None,
            True,
            True,
        ],
        [
            {
                "name": "cnr-server",
                "type": "pip",
                "version": "git+https://github.com/quay/appr@abcdef",
            },
            "pkg:github/quay/appr@abcdef",
            None,
            True,
            True,
        ],
        [
            {
                "name": "operator-manifest",
                "type": "pip",
                "version": (
                    "https://github.com/containerbuildsystem/operator-manifest/archive/"
                    "1234.tar.gz#egg=operator-manifest&cachito_hash=sha256:abcd"
                ),
            },
            (
                "pkg:generic/operator-manifest"
                "?download_url=https%3A%2F%2Fgithub.com%2Fcontainerbuildsystem%2Foperator-manifest"
                "%2Farchive%2F1234.tar.gz%23egg%3Doperator-manifest%26cachito_hash%3Dsha256%3Aabcd"
                "&checksum=sha256:abcd"
            ),
            None,
            True,
            True,
        ],
        [
            {
                "name": "tour",
                "type": "git-submodule",
                "version": (
                    "https://github.com/testrepo/tour.git#58c88e4952e95935c0dd72d4a24b0c44f2249f5b"
                ),
            },
            "pkg:github/testrepo/tour@58c88e4952e95935c0dd72d4a24b0c44f2249f5b",
            None,
            True,
            True,
        ],
        [
            {"name": "grc-ui", "type": "yarn", "version": "1.0.0"},
            "pkg:npm/grc-ui@1.0.0",
            None,
            True,
            True,
        ],
        [
            {
                "name": "security-middleware",
                "type": "yarn",
                "version": "github:open-cluster-management/security-middleware#i0am0a0commit0hash",
            },
            "pkg:github/open-cluster-management/security-middleware@i0am0a0commit0hash",
            None,
            True,
            True,
        ],
        [
            {
                "name": "security-middleware",
                "type": "yarn",
                "version": "gitlab:deep/nested/repo/security-middleware#i0am0a0commit0hash",
            },
            "pkg:gitlab/deep/nested/repo/security-middleware@i0am0a0commit0hash",
            None,
            True,
            True,
        ],
        [
            {
                "name": "fromgit",
                "type": "yarn",
                "version": "git://some.domain/my/project/repo.git#i0am0a0commit0hash",
            },
            (
                "pkg:generic/fromgit?vcs_url=git%3A%2F%2Fsome.domain%2Fmy%2Fproject%2Frepo.git"
                "%23i0am0a0commit0hash"
            ),
            None,
            True,
            True,
        ],
        [
            {
                "name": "fromweb",
                "type": "yarn",
                "version": "https://some.domain/my/project/package.tar.gz",
            },
            (
                "pkg:generic/fromweb?download_url=https%3A%2F%2Fsome.domain%2Fmy%2Fproject"
                "%2Fpackage.tar.gz"
            ),
            None,
            True,
            True,
        ],
        [
            {"name": "fromfile", "type": "yarn", "version": "file:client-default"},
            "generic/fromfile?file%3Aclient-default",
            None,
            True,
            True,
        ],
        [
            {
                "name": "fromunknown",
                "type": "yarn",
                "version": "unknown://some.domain/my/project/package.tar.gz",
            },
            None,
            None,
            True,
            False,
        ],
        [
            {"name": "zeitwerk", "type": "rubygems", "version": "2.4.2"},
            "pkg:gem/zeitwerk@2.4.2",
            None,
            True,
            True,
        ],
        [
            {
                "name": "httpclient",
                "version": f"git+https://github.com/3scale/httpclient.git@{DEP_COMMIT_ID}",
                "type": "rubygems",
            },
            f"pkg:github/3scale/httpclient@{DEP_COMMIT_ID}",
            None,
            True,
            True,
        ],
    ],
)
def test_purl_conversion(package, expected_purl, parent_package_rel_path, defined, known_protocol):
    pkg = Package.from_json(package)
    if defined and known_protocol:
        purl = to_purl(pkg, parent_package_rel_path)
        assert purl == expected_purl
    else:
        msg = f"The PURL spec is not defined for {pkg.type} packages"
        if defined:
            msg = f"Unknown protocol in {pkg.type} package version: {pkg.version}"
        with pytest.raises(ContentManifestError, match=msg):
            to_purl(pkg, parent_package_rel_path)


def test_purl_conversion_bogus_forge():
    package = {"name": "odd", "type": "npm", "version": "github:something/odd"}
    pkg = Package.from_json(package)

    msg = f"Could not convert version {pkg.version} to purl"
    with pytest.raises(ContentManifestError, match=msg):
        to_purl(pkg)


@pytest.mark.parametrize(
    "repo_url, expected_purl",
    [
        ("http://github.com/org/repo-name", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://github.com/org/repo-name/", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://github.com:443/org/repo-name", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://user:pass@github.com/org/repo-name", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://github.com/org/repo-name.git", f"pkg:github/org/repo-name@{GIT_REF}"),
        ("http://bitbucket.org/org/repo-name", f"pkg:bitbucket/org/repo-name@{GIT_REF}"),
        (
            # pkg:gitlab is not defined in the purl spec yet
            "http://gitlab.com/org/repo-name",
            f"pkg:generic/foo?vcs_url=http%3A%2F%2Fgitlab.com%2Forg%2Frepo-name%40{GIT_REF}",
        ),
        (
            "http://gitlab.com/org/repo-name/",
            f"pkg:generic/foo?vcs_url=http%3A%2F%2Fgitlab.com%2Forg%2Frepo-name%40{GIT_REF}",
        ),
        (
            "http://gitlab.com/org/repo-name.git",
            f"pkg:generic/foo?vcs_url=http%3A%2F%2Fgitlab.com%2Forg%2Frepo-name.git%40{GIT_REF}",
        ),
    ],
)
def test_vcs_purl_conversion(repo_url, expected_purl):
    pkg = Package(name="foo", type="", version="")
    assert to_vcs_purl(pkg.name, repo_url, GIT_REF) == expected_purl


@pytest.mark.parametrize(
    "package, path, expected_purl",
    [
        (
            {"type": "gomod", "name": "k8s.io/kubernetes", "version": "v1.0.0"},
            None,
            "pkg:golang/k8s.io%2Fkubernetes@v1.0.0",
        ),
        (
            {"type": "go-package", "name": "k8s.io/kubernetes/cmd/kubectl", "version": "v1.0.0"},
            "cmd/kubectl",
            # no subpath in purl, name already reflects it
            "pkg:golang/k8s.io%2Fkubernetes%2Fcmd%2Fkubectl@v1.0.0",
        ),
        (
            {
                "type": "git-submodule",
                "name": "foo",
                "version": f"https://github.com/org/foo#{GIT_REF}",
            },
            "foo",
            # no subpath in purl, it points to different repo
            f"pkg:github/org/foo@{GIT_REF}",
        ),
    ],
)
def test_top_level_purl_conversion_specialized(package, path, expected_purl, default_request):
    """Test top-level purl conversion for package types that can use specialized purls."""
    pkg = Package(**package)
    purl = to_top_level_purl(pkg, default_request, subpath=path)
    assert purl == expected_purl


@pytest.mark.parametrize("pkg_manager", ["npm", "pip", "yarn"])
@pytest.mark.parametrize(
    "path, expected_purl", [(None, DEFAULT_PURL), ("some/subpath", f"{DEFAULT_PURL}#some/subpath")]
)
def test_top_level_purl_conversion_generic(pkg_manager, path, expected_purl, default_request):
    """Test top-level purl conversion for package types that must use generic purls."""
    pkg = Package(name="foo", version="1.0.0", type=pkg_manager)
    purl = to_top_level_purl(pkg, default_request, subpath=path)
    assert purl == expected_purl


def test_top_level_purl_conversion_bogus(default_request):
    pkg = Package(name="foo", version="1.0.0", type="bogus")

    msg = "'bogus' is not a valid top level package"
    with pytest.raises(ContentManifestError, match=msg):
        to_top_level_purl(pkg, default_request)


def test_get_local_go_package_dep_purl_not_local(default_request: Request):
    packages_json = [
        {
            "name": "example.com/org/project/foo",
            "type": "go-package",
            "version": "1.0.0",
            "dependencies": [
                {
                    "name": "example.com/org/project/bar",
                    "type": "go-package",
                    "version": "1.0.0",
                },
            ],
        },
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[0]
    dependency = package.dependencies[0]

    cm = ContentManifest(default_request, packages)

    msg = f"{dependency} has an invalid version for a local dependency"
    with pytest.raises(ValueError, match=msg):
        cm._get_local_go_package_dep_purl(package, dependency)


def test_get_local_go_package_dep_purl_no_pkg_module(default_request: Request):
    packages_json = [
        {
            "name": "example.com/org/project/foo",
            "type": "go-package",
            "version": "1.0.0",
            "dependencies": [
                {
                    "name": "example.com/org/project/bar",
                    "type": "go-package",
                    "version": "../bar",
                },
            ],
        },
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[0]
    dependency = package.dependencies[0]

    cm = ContentManifest(default_request, packages)

    msg = f"Could not find parent Go module for package: {package.name}"
    with pytest.raises(RuntimeError, match=msg):
        cm._get_local_go_package_dep_purl(package, dependency)


def test_get_local_go_package_dep_purl_no_dep_module(default_request: Request):
    packages_json = [
        {
            "name": "example.com/org/project/foo",
            "type": "gomod",
            "version": "1.0.0",
            "dependencies": [],
        },
        {
            "name": "example.com/org/project/foo",
            "type": "go-package",
            "version": "1.0.0",
            "dependencies": [
                {
                    "name": "example.com/org/project/bar",
                    "type": "go-package",
                    "version": "../bar",
                },
            ],
        },
    ]

    packages = _load_packages_from_json(packages_json)
    package = packages[1]
    dependency = package.dependencies[0]

    cm = ContentManifest(default_request, packages)

    msg = f"Could not find parent Go module for package: {dependency.name}"
    with pytest.raises(RuntimeError, match=msg):
        cm._get_local_go_package_dep_purl(package, dependency)
