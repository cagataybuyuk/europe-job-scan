# GD-004 ADP Preference Safe-Fill Canary

## Evidence

Live cookie-preference inspection run `34983959851` opened the OneTrust preference center with exactly one reviewed click and zero cookie/application mutations. The resulting reviewed surface exposed unique visible enabled `Unselect All` and `Save Changes` actions and no visible checkbox controls.

Reviewed preference-center fingerprint:

`f80faea8117621a286097dec92e55510843187ec9fdc82792379330e4034bd3c`

The reviewed ADP application-entry surface remains:

`567e7890f5a01f151dbeeb23851ad7cf5fb8100a32c3e0386227d17cd507d313`

The reviewed post-Apply identity surface remains:

`d8b72afea27912bd5e280d8a819ec836e44c2982faa3a3179bbb06c7aa58aa7f`

## Runtime authority

The canary is allowed to execute only this bounded sequence in one ephemeral browser context:

1. open the unique visible OneTrust `To manage your preferences, click here` control;
2. require the exact reviewed preference-center fingerprint;
3. click the unique visible enabled `Unselect All` action;
4. click the unique visible enabled `Save Changes` action;
5. require the cookie banner and preference center to be gone;
6. re-snapshot and require the exact reviewed ADP navigation fingerprint again;
7. click exactly one reviewed Apply action;
8. require the exact reviewed ADP identity surface;
9. write only first name, last name and email when their current values differ;
10. require exact readback and browser validity after every write;
11. stop and record read-only button accessibility evidence.

The canary never directly manipulates a cookie checkbox and never accepts optional cookies.

## Candidate profile privacy

Candidate values come only from the environment-scoped `EJS_ADP_CANARY_PROFILE_JSON` secret. Runtime evidence stores hashes and boolean readback/validity results, not raw candidate values.

## Explicitly forbidden

- cookie acceptance;
- direct checkbox `check()` / `uncheck()` operations;
- force clicks or retry loops;
- credentials;
- phone or country mutation;
- select-option changes;
- CV/file upload;
- CAPTCHA/challenge bypass;
- a second application navigation action;
- final Submit.

## Approval gate

The workflow is manually dispatched at an immutable main SHA and uses the protected `gd004-safe-fill-upload-canary` environment. Its exact approval phrase is:

`APPROVE-TEST-ADP-UNSELECT-OPTIONAL-COOKIES-AND-IDENTITY-SAFE-FILL`

Maximum mutation authority per run is three reviewed cookie-preference clicks, one reviewed Apply click, and at most three AUTO_SAFE identity `fill()` operations. File upload and final submit remain disabled.
