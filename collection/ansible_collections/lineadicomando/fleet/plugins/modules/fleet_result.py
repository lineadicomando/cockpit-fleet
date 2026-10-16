# SPDX-License-Identifier: LGPL-2.1-or-later
# Documentation only: the work is done by the action of the same name, on the
# controller, and nothing is sent to the node.

DOCUMENTATION = r"""
module: fleet_result
short_description: Give cockpit-fleet the result of a check written as a playbook
description:
  - The last task of a check of the user, on each host. It hands back a state,
    a summary, metrics and details in the result contract of cockpit-fleet,
    which applies the thresholds of the check to the metrics when it takes
    the result in, and keeps the worse of its state and theirs.
  - It runs on the controller and sends nothing to the node, so it is the
    same for Linux and Windows hosts.
options:
  state:
    description: C(ok), C(warning), C(critical) or C(unknown), or 0 to 3.
    type: raw
    default: ok
  summary:
    description: One line for a person, at most 500 characters.
    type: str
    default: ""
  metrics:
    description:
      - Measures, each with C(name) (lowercase letters, digits and C(_)),
        C(value), and optionally C(unit), C(instance), C(min) and C(max).
      - A threshold of the check applies to every metric of its name.
    type: list
    elements: dict
    default: []
  details:
    description: Anything else worth keeping, shown with the result.
    type: dict
    default: {}
author:
  - Alessandro Gagliano
"""

EXAMPLES = r"""
- name: Count the mails waiting in the queue
  ansible.builtin.command: postqueue -j
  register: queue
  changed_when: false

- name: Tell cockpit-fleet
  lineadicomando.fleet.fleet_result:
    summary: "{{ queue.stdout_lines | length }} mails waiting"
    metrics:
      - {name: queued, value: "{{ queue.stdout_lines | length }}"}
"""

RETURN = r"""
fleet_result:
  description: What was given, checked.
  type: dict
  returned: always
"""
