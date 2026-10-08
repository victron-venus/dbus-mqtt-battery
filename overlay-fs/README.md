# Overlay filesystem helpers

These shell scripts are derived from
[mr-manuel/venus-os_overlay-fs](https://github.com/mr-manuel/venus-os_overlay-fs/tree/efa611bafdea2ee294b4105cb509919d144ff1e2),
version 0.0.1 (2024-11-28), under the [MIT license](LICENSE).
The upstream copyright notices remain in each script.

This repository carries local adaptations, including named shell helpers,
Bash conditional syntax and error diagnostics on standard error. These files
are not unmodified upstream copies. Compare local changes with the pinned
upstream revision before updating them; preserve the license and copyright
notices in source and release archives.

The helpers modify overlay mounts and persistent configuration on a Venus OS
device. Follow the upstream usage documentation for operation. Local syntax
and isolated command tests do not establish compatibility with a particular
device or firmware release.
