#!/bin/sh
# Workflows through fleetctl: three steps with an approval at the second,
# given by another user; what set_stats sets passed on, never rendered; a
# failed step handled by a step for it; a run resumed from its failed step;
# a workflow canceled.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
SRC=/srv/fleet-it
require_wipe

cleanup() { wipe; }
trap cleanup EXIT
cleanup
prepare 4
useradd -m -G cockpit-fleet-approver itapprover

mkdir -p "$SRC/flow"
cat > "$SRC/flow/build.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.set_stats:
        data:
          version: "7"
          note: !unsafe "{{ lookup('pipe', 'id') }}"
        per_host: false
YAML
cat > "$SRC/flow/deploy.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.debug:
        msg: "deploying version {{ version }} with note {{ note }} as {{ colour }}"
YAML
cat > "$SRC/flow/flaky.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.fail:
        msg: the flag says so
      when: lookup('ansible.builtin.file', 'flag.txt') == 'fail'
YAML
cat > "$SRC/flow/wait.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.pause:
        seconds: 300
YAML
printf fail > "$SRC/flow/flag.txt"
chmod -R a+rX "$SRC"

project=$(get "$(admin project.create "{\"name\": \"flow\", \"kind\": \"local\", \"path\": \"$SRC/flow\"}")" 'a["data"]["id"]')
inventory=$(get "$(admin inventory.create '{"name": "local", "kind": "managed", "content": "all:\n  hosts:\n    localhost:\n      ansible_connection: local\n"}')" 'a["data"]["id"]')
template() {  # template NAME PLAYBOOK [EXTRA-JSON]: its id
    get "$(admin template.create "{\"name\": \"$1\", \"project_id\": \"$project\", \"playbook\": \"$2\", \"inventory_id\": \"$inventory\"${3:-}}")" 'a["data"]["id"]'
}
build=$(template build build.yml)
deploy=$(template deploy deploy.yml ', "survey": [{"name": "colour", "type": "text", "required": true}]')
report=$(template report build.yml)
flaky=$(template flaky flaky.yml)
waits=$(template wait wait.yml)

step_run() {  # step_run WORKFLOW_RUN NODE: the id of the latest run of a step
    db "SELECT (SELECT id FROM runs WHERE workflow_run_id = '$1' AND workflow_node = '$2' ORDER BY created_at DESC LIMIT 1)"
}
wait_step() {  # wait_step WORKFLOW_RUN NODE SECONDS: until the step has a run
    n=0
    while [ "$n" -lt "$3" ] && [ "$(step_run "$1" "$2")" = None ]; do sleep 1; n=$((n + 1)); done
    step_run "$1" "$2"
}

# --- three steps, an approval at the second ---------------------------------------
workflow=$(admin workflow.create "{\"name\": \"release\", \"nodes\": [
    {\"id\": \"build\", \"template_id\": \"$build\"},
    {\"id\": \"deploy\", \"template_id\": \"$deploy\", \"answers\": {\"colour\": \"blue\"}, \"needs_approval\": true},
    {\"id\": \"report\", \"template_id\": \"$report\"}],
  \"edges\": [{\"from\": \"build\", \"to\": \"deploy\", \"condition\": \"success\"},
              {\"from\": \"deploy\", \"to\": \"report\", \"condition\": \"success\"}]}")
workflow_id=$(get "$workflow" 'a["data"]["id"]')
expect_eq "an administrator creates a workflow" "$(get "$workflow" 'len(a["data"]["nodes"])')" 3
expect_eq "an operator cannot" "$(get "$(ctl itoperator workflow.create '{"name": "x", "nodes": []}')" 'a["error"]["code"]')" forbidden
expect_eq "a template a workflow uses is not deleted" "$(get "$(admin template.delete "{\"id\": \"$report\"}")" 'a["error"]["code"]')" conflict

run=$(ctl itoperator workflow.launch "{\"id\": \"$workflow_id\"}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_eq "an operator launches it" "$(get "$run" 'a["data"]["kind"], a["data"]["status"]')" "workflow running"
first=$(wait_step "$run_id" build 10)
expect_run "the first step runs" "$first" 120 successful
expect_eq "it kept what set_stats set" "$(db "SELECT json_extract(artifacts, '$.version') || ' ' || json_extract(artifacts, '$.note') FROM runs WHERE id = '$first'")" "7 {{ lookup('pipe', 'id') }}"
second=$(wait_step "$run_id" deploy 30)
expect_eq "the second waits for its approval" "$(wait_status "$second" 5)" pending_approval
sleep 3
expect_eq "and nothing after it starts" "$(step_run "$run_id" report)" None
expect_eq "who launched the workflow cannot approve it" \
    "$(get "$(ctl itoperator approval.approve "{\"id\": \"$second\"}")" 'a["error"]["code"]')" forbidden
expect_eq "another user approves it" \
    "$(get "$(ctl itapprover approval.approve "{\"id\": \"$second\"}")" 'a["data"]["status"]')" running
expect_run "the second step runs" "$second" 120 successful
log=$(sed 's/\x1b\[[0-9;]*m//g' "/var/lib/cockpit-fleet/runs/$second/stdout.log")
case "$log" in
    *"deploying version 7 with note"*" as blue"*) pass "it got what the first passed on, and its answer" ;;
    *) fail "it got what the first passed on, and its answer: $(printf '%s' "$log" | grep -i 'deploying\|error' | head -3)" ;;
esac
case "$log" in
    *"note {{ lookup('pipe', 'id') }} as blue"*) pass "what was passed on is not rendered" ;;
    *) fail "what was passed on is not rendered: $(printf '%s' "$log" | grep deploying)" ;;
esac
third=$(wait_step "$run_id" report 30)
expect_run "the third step runs" "$third" 120 successful
expect_run "the workflow completes" "$run_id" 30 successful
progress=$(ctl itoperator workflow.progress "{\"id\": \"$run_id\"}")
expect_eq "its progress shows the three steps done" "$(get "$progress" '[n["state"] for n in a["data"]["nodes"]]')" "['success', 'success', 'success']"
expect_eq "the approval is in the audit log" \
    "$(get "$(admin audit.list)" 'any(e["command"]=="approval.approve" and e["user"]=="itapprover" for e in a["data"]["entries"])')" True

# --- a failure, a step for it, and resuming -------------------------------------
flow=$(get "$(admin workflow.create "{\"name\": \"flaky\", \"nodes\": [
    {\"id\": \"build\", \"template_id\": \"$build\"},
    {\"id\": \"flaky\", \"template_id\": \"$flaky\"},
    {\"id\": \"deploy\", \"template_id\": \"$deploy\", \"answers\": {\"colour\": \"red\"}},
    {\"id\": \"cleanup\", \"template_id\": \"$report\"}],
  \"edges\": [{\"from\": \"build\", \"to\": \"flaky\", \"condition\": \"success\"},
              {\"from\": \"flaky\", \"to\": \"deploy\", \"condition\": \"success\"},
              {\"from\": \"build\", \"to\": \"cleanup\", \"condition\": \"failure\"}]}")" 'a["data"]["id"]')
run_id=$(get "$(ctl itoperator workflow.launch "{\"id\": \"$flow\"}")" 'a["data"]["id"]')
expect_run "a workflow with a step that fails fails" "$run_id" 240 failed
expect_eq "the steps after it were skipped" \
    "$(get "$(ctl itoperator workflow.progress "{\"id\": \"$run_id\"}")" '[n["state"] for n in a["data"]["nodes"]]')" "['success', 'failure', 'skipped', 'skipped']"
printf ok > "$SRC/flow/flag.txt"
again=$(ctl itoperator workflow.resume "{\"id\": \"$run_id\"}")
again_id=$(get "$again" 'a["data"]["id"]')
expect_run "resumed, it completes" "$again_id" 240 successful
progress=$(ctl itoperator workflow.progress "{\"id\": \"$again_id\"}")
expect_eq "the step that had succeeded was not run again" "$(get "$progress" '[n["carried"] for n in a["data"]["nodes"]]')" "[True, False, False, False]"
expect_eq "and still passed on what it had set" \
    "$(sed 's/\x1b\[[0-9;]*m//g' "/var/lib/cockpit-fleet/runs/$(step_run "$again_id" deploy)/stdout.log" | grep -c 'deploying version 7')" 1

# --- a checkpoint: a confirmation who launched it gives --------------------------
check=$(get "$(admin workflow.create "{\"name\": \"checkpoint\", \"nodes\": [
    {\"id\": \"build\", \"template_id\": \"$build\"},
    {\"id\": \"report\", \"template_id\": \"$report\", \"approval\": \"confirm\"}],
  \"edges\": [{\"from\": \"build\", \"to\": \"report\", \"condition\": \"success\"}]}")" 'a["data"]["id"]')
run_id=$(get "$(ctl itoperator workflow.launch "{\"id\": \"$check\"}")" 'a["data"]["id"]')
expect_run "the step before the checkpoint runs" "$(wait_step "$run_id" build 10)" 120 successful
second=$(wait_step "$run_id" report 30)
expect_eq "the step after waits for a confirmation" \
    "$(wait_status "$second" 5) $(get "$(ctl itoperator run.get "{\"id\": \"$second\"}")" 'a["data"]["approval"]["mode"], a["data"]["approval"]["can_decide"]')" \
    "pending_approval confirm True"
expect_eq "who launched it confirms it, without the right to approve" \
    "$(get "$(ctl itoperator approval.approve "{\"id\": \"$second\"}")" 'a["data"]["approval"]["decided_by"]')" itoperator
expect_run "and the workflow completes" "$run_id" 120 successful

# --- cancel --------------------------------------------------------------------
slow=$(get "$(admin workflow.create "{\"name\": \"slow\", \"nodes\": [
    {\"id\": \"wait\", \"template_id\": \"$waits\"}, {\"id\": \"after\", \"template_id\": \"$report\"}],
  \"edges\": [{\"from\": \"wait\", \"to\": \"after\", \"condition\": \"always\"}]}")" 'a["data"]["id"]')
run_id=$(get "$(ctl itoperator workflow.launch "{\"id\": \"$slow\"}")" 'a["data"]["id"]')
waiting=$(wait_step "$run_id" wait 10)
sleep 3
ctl itoperator run.cancel "{\"id\": \"$run_id\"}" > /dev/null
expect_run "a canceled workflow ends canceled" "$run_id" 60 canceled
expect_eq "its running step was canceled" "$(db "SELECT status FROM runs WHERE id = '$waiting'")" canceled
expect_eq "and the step after it never started" "$(step_run "$run_id" after)" None

finish
