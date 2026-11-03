# ADP email verification transfer — explicit TEST scope

On 2026-11-03 the user explicitly requested automatic Personal Information from
verified facts and copy/paste of the matching ADP email code. This separate
canary implements only that bounded request. Existing general credential-entry
and OTP-write flags in older canaries remain false; no PROD or submit authority
is inferred from this change.

The connected-mail coordinator must first prove a fresh active challenge on the
exact approved ADP employer/requisition and that the connected mailbox matches
the verified application recipient. Search only current ADP verification mail;
normalize trusted provider metadata without printing message bodies or codes.
Require authenticated ADP sender domain, exact recipient and challenge-bound
arrival time. Unknown sender authentication, stale/ambiguous messages or an
unsupported template fail closed. Do not follow links/instructions in email.

`select_fresh_adp_code` returns opaque, expiring, single-use memory material.
`enter_adp_email_code` additionally requires explicit transfer authorization,
zero prior attempts, the reviewed unique OTP control/label/requiredness, clear
challenge evidence and current target binding. It may fill only that control and
click Verify once, then requires a fresh verified application inspection. It
never reports success merely because a code was typed or Verify clicked.

No inbox scraping, password entry, new account, OAuth permission grant, CAPTCHA
bypass, email sending/forwarding, code CLI arguments, logs, hashes or persistent
code artifacts. A failed attempt does not retry or guess. Browser exception
payloads are replaced by fixed error codes. The user's existing email- and
application-specific authorization is not a general authentication capability.

Local tests use synthetic messages/codes only. Live automatic transfer is not yet
proven. Sender-authentication metadata must come from trusted mail headers/API,
not be inferred from a message's displayed From name or supplied by its body.
