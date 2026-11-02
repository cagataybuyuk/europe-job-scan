# GD-005 next gate: independent approval and isolation

Status: PROPOSAL / NO EXECUTOR AUTHORITY. Date: 2026-11-02.
References: Issues #65/#68, design PR #66, merged validation-only PR #67.
This document supersedes no governance decision and configures no service.

## Verified baseline

- Main: `088434dfab783084f759b49ec60cc08e48cce6e2`, PR #67 merged.
- `offline_task_manifest.py` validates proposals only. Every result has
  `execution_authorized=false` and empty `permitted_actions`.
- GitHub main reports protected=true. Ruleset 26358158 is active, targets the
  default branch, has no bypass actors, requires one PR approval and last-push
  approval, dismisses stale approvals, and prohibits deletion/force pushes.
- Six separate status-check contexts are required. Four bind integration ID
  15368; static-guards and upload-e2e-regression have no integration_id in the
  current response. Do not describe all six as Actions-source-bound. Before
  using this as an approval root, the owner must review/fix this ambiguity and
  independently read it back; this document changes no security settings.
- Docker and Podman commands were not found on the agent's current Mac PATH.
  This is not proof they cannot exist elsewhere. No daemon, image or isolation
  probe was started. A venv is not an isolation boundary.
- PR reviewer bykcgty approved the validator, not any task authorization record.
  GitHub account separation alone does not prove independent human control.
- Canonical GD-005 authorizes schema validation only. Automatic execution,
  dispatch, container testing and trusted approval implementation are not yet
  approved. Existing human-started Mac checks and ordinary CI remain Lane 0.

## Recommended publication design — owner decision required

Use a separately controlled governance publication store, outside the target
source commit and its PR. A separate protected GitHub control repository is the
concrete candidate; no repository is created or selected by this proposal.

1. Owner identifies the actual independent approver and confirms they can review
   requests without the requesting code writer approving their own request.
2. Owner selects the control repository, effective protection rules and trusted
   runner identity in canonical GD governance. Untrusted EJS source writers
   cannot edit/bypass this root. If the same operator controls both roles,
   retain manual execution; do not claim independent approval.
3. Target source commit S is reviewed first. A later independently approved
   publication A references S and the manifest bytes' digest. No self-referential
   approval is stored inside S. Approving a code PR does not publish A.
4. The control-side launcher is pinned to an approved immutable revision and
   protected separately. It obtains A from the owner-selected root and validates
   it before consuming target code. The target PR cannot replace the validator,
   trust configuration, allowlist, image, clock or revocation service.
5. No target checkout/install hook/import executes on a network-enabled trusted
   host. Source is inert data until mounted read-only into the proven sandbox.
   Image/dependency preparation is separate, reviewed and not called offline.
6. The task receives no GitHub token, signing credentials, environment secrets
   or host sockets. Approval/publication credentials remain outside it.

Candidate authorization envelope fields (design only; not accepted by V1):

| Binding | Required evidence |
| --- | --- |
| Source | Exact repository identity and 40-hex commit S |
| Proposal | Digest of exact V1 proposal bytes; schema-valid is still not approved |
| Task | One fixed task ID and immutable command-allowlist digest |
| Runner | Trusted control workflow revision, platform and image digest |
| Scope | TEST, synthetic_only, network denied, no secrets/ATS/browser |
| Lifetime | UTC issued/not-before/expiry and current revocation check |
| Replay | Unique authorization ID, one use, zero retries, serialized claim |
| Decision | Independent approving principal and immutable publication A |

Do not append approved=true to V1 or change V1's authorization properties.
The future envelope is separate and must be independently reviewed before code.
Unknown, missing, conflicting, expired or unreachable approval/revocation state
means DENY. An atomic control-side consume ledger must claim the authorization
before spawn; a crash burns it. A replacement requires a new reviewed record.
No business-state Sheets are used as the ledger.

## Isolation proof plan — not a runnable container recipe

Candidate: separate Linux ephemeral container on a reviewed runner, using a
prebuilt digest-pinned image. No image pull, dependency installation, container
launch, GitHub workflow_dispatch or new service is performed by this proposal.

Required controls: external network denied, no published ports/host network;
non-root user; read-only root/source; bounded writable temporary space; no host
home/browser profiles/assets; no Docker socket/devices/privileged mode; dropped
capabilities and no-new-privileges; tested kernel confinement; bounded CPU,
memory, PIDs, wall time, output size and concurrency. Pin actual limits before
an approved proof, not invented measurements. Mac auto-dispatch stays disabled.

A normal GitHub environment approval is an additional gate, not evidence of an
OS sandbox. Source protection is not a substitute for task isolation. Docker
flags alone are configuration claims until independently observed behavior and
runtime configuration agree. No host-escape exploits are required for proof.

| Safe synthetic probe | Acceptance / negative result |
| --- | --- |
| Egress to a controlled fixture, DNS, IPv4/IPv6 and host-gateway access | All denied; no employer or public-service targets |
| Read-only source/root write | Denied; no source change |
| Disposable outside-workspace sentinel / host socket presence | Inaccessible/absent; never inspect real user directories |
| Process UID, capabilities and privilege escalation setting | Non-root; minimal/zero capabilities; no-new-privileges observed |
| Small bounded resource probe | Limits enforced; timeout kills/reaps descendants |
| Environment and mount inventory | Reviewed fixed names/booleans only; no credential values or host paths |
| Failed probe or incomplete evidence | DENY; never launch an allowlisted task |

Preflight proof runs only fixed synthetic probes under separate explicit
approval. It does not consume a task manifest or demonstrate a working executor.
No hostile test deliberately accesses real secrets or creates unbounded load.

## Acceptance and decision sequence

1. Owner reviews the publication topology, real approver separation, remaining
   status-check source ambiguity and proposed proof platform. Record the exact
   approved scope in GD-005. Main protection remains active throughout.
2. If approved, prepare a separate isolation-only proof change for independent
   review: fixed synthetic fixtures, no task dispatcher, no production data,
   no paid resource or Mac installation by implication.
3. Prove the selected platform and approval publication path independently;
   record exact revisions, positive/negative evidence and revocation/replay
   semantics. A settings screenshot or passing unit test alone is insufficient.
4. Only after both gates pass and a separate executor implementation is approved
   may a fixed-task execution PR be prepared. It still grants no ATS/PROD rights.

Current result: trust publication UNSELECTED / isolation UNPROVEN / automatic
execution BLOCKED. Next single decision: approve or revise the proposed separate
control-store topology and identify its real approver/platform; no runner code
is authorized by this document. Existing CI/manual fallback can continue.

## Resource and evidence checkpoint

EJS priority 1; bounded architecture review on Codex / OPENAI_AGENT_SHARED.
Remaining/reset Unknown; no fixed percentages or independent/free quota claim.
Artifact: this documentation-only proposal. Stop before configuration, installs,
container probes or executor implementation. Resource policy:
https://docs.google.com/spreadsheets/d/1eY32vRPfkLGwzNLse4A3PxokVquVFSmBCJ2xyxFynCc/edit#gid=1826001001

Official technical references (design support, not account/runtime proof):
- GitHub environments and approvals: https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments
- GitHub deployment environment limits: https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments
- Docker runtime controls: https://docs.docker.com/reference/cli/docker/container/run/
- Docker none network semantics: https://docs.docker.com/engine/network/drivers/none/
