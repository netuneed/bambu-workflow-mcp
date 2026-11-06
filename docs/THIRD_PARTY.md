# Third-party components

Own code is MIT; see [LICENSE](../LICENSE). The dependency inventory originated on 2026-10-25 and was updated for filelock on 2026-11-06. It is not a claim that every allowed future dependency version has the same license or has passed advisory review.

## Direct dependencies

| Component | Project range | Version checked | License |
| --- | --- | --- | --- |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | `>=1.20,<2` | 1.30.0 | MIT |
| [paho-mqtt](https://github.com/eclipse-paho/paho.mqtt.python) | `>=2.1,<3` | 2.1.0 | EPL-2.0 or EDL-1.0 (BSD-3-Clause) |
| [trimesh](https://github.com/mikedh/trimesh) | `>=4.9,<5` | 4.12.2 | MIT |
| [NumPy](https://github.com/numpy/numpy) | `>=2,<3` | 2.5.4 | Build metadata: BSD-3-Clause, 0BSD, MIT, Zlib, CC0-1.0 |
| [keyring](https://github.com/jaraco/keyring) | `>=25,<26` | 25.7.0 | MIT |
| [filelock](https://github.com/tox-dev/filelock) | `>=4.2.1,<5` | 4.2.1 | MIT |

Development tools checked: pytest 9.1.1 (MIT, range `>=8,<10`), build 1.6.1 (MIT, range `>=1,<2`). The isolated build backend hatchling uses MIT (`>=1.27,<2`). Release packaging must retain notices/licenses for the actual direct and transitive versions distributed. NumPy binary wheels carry additional bundled-component notices; one BSD label cannot replace them.

Primary license references: [paho](https://github.com/eclipse-paho/paho.mqtt.python/blob/master/LICENSE.txt), [filelock](https://github.com/tox-dev/filelock/blob/main/LICENSE), [Hatch](https://github.com/pypa/hatch/blob/master/LICENSE.txt).

The 2026-11-06 exact-version advisory check identified filelock 3.32.7 as affected by published advisories, including [GHSA-8mx5-7m5m-r77g](https://github.com/advisories/GHSA-8mx5-7m5m-r77g) and [GHSA-69hj-f68j-2879](https://github.com/advisories/GHSA-69hj-f68j-2879). The declared range and runtime lock now require 4.2.1 or later within major version 4. Advisory checks are time-specific and do not certify absence of undisclosed vulnerabilities.

## Separately installed software

- [Bambu Studio](https://github.com/bambulab/BambuStudio): AGPL-3.0. The owner installs it separately and the workflow invokes its CLI. Neither its executable nor source profiles are bundled.
- Bambu's optional proprietary networking plugin contains non-free components. It is not bundled and is not needed by the selected LAN adapter.
- Blender and the chosen Blender MCP are installed separately. Their licenses depend on the selected upstream package and do not become this project's license.
- [Support Fins v1.0.5](https://github.com/gittrahan/support-fins/tree/514549694e816999a5e4c6674829eba7ea852019): MIT, copyright 2026 gittrahan. An optional checksum-pinned local CLI can prepare derivatives. Upstream code/assets are not bundled by this project. If redistributed later, preserve its MIT notice and the notices of its bundled components. [Pinned license](https://github.com/gittrahan/support-fins/blob/514549694e816999a5e4c6674829eba7ea852019/LICENSE).
- Fusion and QymCAD are export sources, not runtime dependencies. Native applications, documents, and installers are not included. Autodesk's software terms and [QymCAD's upstream license](https://github.com/QymIs-Tech/QymCAD/blob/bb5ff2dff25c90fd77b5ff7387ea6380d53d34bd/LICENSE) apply to those products independently.

Bambu Connect, cloud credentials, FULU OrcaSlicer-bambulab, and online printfins uploads are not dependencies of this local workflow. A local derivative does not authorize uploading a private model to a website.

Installed profiles are resolved into local job state along with model/manifest artifacts, not distributed in the package. Redistribution of those local files would require reviewing source-profile and model rights. [Bambu Studio license](https://github.com/bambulab/BambuStudio/blob/master/LICENSE) and [upstream README](https://github.com/bambulab/BambuStudio/blob/master/README.md) describe its boundary.
