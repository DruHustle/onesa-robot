# NVIDIA reference model and renderer notices

The three local visual references derive from NVIDIA's
[`nvidia/simready-assets` dataset at revision `4763fbfaa4f8e97658e68b57d01b4c86659e08b5`](https://huggingface.co/datasets/nvidia/simready-assets/tree/4763fbfaa4f8e97658e68b57d01b4c86659e08b5).
Each included asset has its own supplied Creative Commons Attribution 4.0
International license. These statements apply to the listed assets, not to all
NVIDIA content or to Onesa's project source.

| Browser reference | NVIDIA source asset | Local directory |
| --- | --- | --- |
| Transfer conveyor | `conveyor_belt_a61` | `public/models/nvidia/nvidia-transfer-conveyor` |
| Mobile cart | `cart_mobile_e01` | `public/models/nvidia/nvidia-mobile-cart` |
| Warehouse rack | `rack_long_empty_a01` | `public/models/nvidia/nvidia-storage-rack` |

Models by NVIDIA, from `nvidia/simready-assets`, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Converted and optimized
for browser viewing by Onesa UG: geometry simplified and materials adapted.
The independently copied GLBs and previews retain the source package records and
unmodified license texts. `manifest.json` pins every local file's SHA256 and size;
each `SOURCE.json` records original package hashes and conversion details.

The UI repeats the asset attribution and links to the pinned original, license
and conversion record. Attribution does not imply NVIDIA endorsement. The
derivatives preserve static visual geometry only; simulation physics, sensors,
articulation, collision behavior and physical safety are not validated by them.

The browser renderer uses [Three.js](https://github.com/mrdoob/three), version
0.186.1, under the MIT license, copyright 2010–2026 three.js authors. The npm
lockfile records the exact package integrity. The build copies the required
upstream modules, changes only local import specifiers and retains the upstream
license at `dist/vendor/three/LICENSE.txt`. No project-wide license is granted.
