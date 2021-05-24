# Copyright 2018 Easymov Robotics
# Licensed under the Apache License, Version 2.0

import os
import shutil
from pathlib import Path

from colcon_ros.task.ament_cargo import CARGO_EXECUTABLE
from colcon_core.environment import create_environment_scripts
from colcon_core.logging import colcon_logger
from colcon_core.plugin_system import satisfies_version
from colcon_core.shell import create_environment_hook, get_command_environment
from colcon_core.task import run
from colcon_core.task import TaskExtensionPoint

import toml

logger = colcon_logger.getChild(__name__)


class AmentCargoBuildTask(TaskExtensionPoint):
    """Build Cargo packages."""

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(TaskExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')

    def add_arguments(self, *, parser):  # noqa: D102
        parser.add_argument(
            '--cargo-args',
            nargs='*', metavar='*', type=str.lstrip,
            help='Pass arguments to Cargo projects. '
            'Arguments matching other options must be prefixed by a space,\n'
            'e.g. --cargo-args " --help"')

    async def build(  # noqa: D102
        self, *, additional_hooks=[], skip_hook_creation=False
    ):
        pkg = self.context.pkg
        args = self.context.args

        logger.info(
            "Building Cargo package in '{args.path}'".format_map(locals()))

        try:
            env = await get_command_environment(
                'build', args.build_base, self.context.dependencies)
        except RuntimeError as e:
            logger.error(str(e))
            return 1

        self.progress('prepare')
        rc = self.prepare_build_dir(env)
        if rc != 0:
            return rc
        self.progress('build')

        root_dir = os.path.join(
            args.install_base, 'lib', self.context.pkg.name)

        # invoke build step
        if CARGO_EXECUTABLE is None:
            raise RuntimeError("Could not find 'cargo' executable")
        cargo_args = self.context.args.cargo_args
        if cargo_args is None:
            cargo_args = []
        cmd = [
            CARGO_EXECUTABLE, 'ros-install',
            '--manifest-path', args.build_base + '/Cargo.toml',
            '--install-base', args.install_base
            ] + cargo_args

        rc = await run(
            self.context, cmd, cwd=args.build_base, env=env)
        if rc and rc.returncode:
            return rc.returncode

        additional_hooks = create_environment_hook(
            'ament_prefix_path', Path(args.install_base),
            self.context.pkg.name, 'AMENT_PREFIX_PATH', '', mode='prepend')

        if not skip_hook_creation:
            create_environment_scripts(
                pkg, args, additional_hooks=additional_hooks)
        

    def prepare_build_dir(self, env):
        prefixes, unresolved = resolve_prefixes(env, self.context.pkg.get_dependencies())

        # Clean up build dir
        build_dir = Path(self.context.args.build_base)
        if build_dir.exists():
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True)

        # Write the resolved Cargo.toml
        content = {}
        cargo_toml = self.context.pkg.path / 'Cargo.toml.in'
        try:
            content = toml.load(str(cargo_toml))
        except toml.TomlDecodeError:
            logger.error('Decoding error when processing "%s"'
                         % cargo_toml.absolute())
            return 1

        for dep in self.context.pkg.get_dependencies():
            prefix = prefixes.get(dep)
            if prefix is None:
                logger.info("Dependency '{}' is not a Rust crate and is not added to Cargo.toml.".format(dep))
                continue
            dep_location = prefix / 'share' / dep / 'rust'
            content['dependencies'][dep] = {'path': str(dep_location)}
        cargo_toml_out = build_dir / 'Cargo.toml'
        toml.dump(content, cargo_toml_out.open('w'))

        # symlink the source folder
        src_dir = Path(self.context.pkg.path).resolve()
        # hacky
        (build_dir / 'src').symlink_to(src_dir / 'src')
        if (src_dir / 'build.rs').is_file():
            (build_dir / 'build.rs').symlink_to(src_dir / 'build.rs')
        return 0


def resolve_prefixes(env, dependencies):
    """
    Find out which prefix contains each of the dependencies.

    :param env: environment dict for this package 
    :param dependencies: package names of dependencies
    :returns: A mapping of dependencies to prefixes and a list of dependencies
    that were not found.
    :rtype Tuple[dict(str, Path), list(path)]
    """
    prefix_for_dependency = {}
    for prefix in env['AMENT_PREFIX_PATH'].split(os.pathsep):
        prefix = Path(prefix)
        packages_dir = prefix / 'share/ament_index/resource_index/rust_crates'
        packages = set(path.name for path in packages_dir.iterdir()) if packages_dir.exists() else set()
        found_dependencies = packages.intersection(dependencies)
        for pkg in found_dependencies:
            prefix_for_dependency[pkg] = prefix
        dependencies -= found_dependencies

    return prefix_for_dependency, dependencies
