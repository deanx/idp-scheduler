

#: The environment a real extraction needs (`idp_client.make_idp_adapter`
#: calls `_require` on exactly these). Published so a caller can ask
#: "could this machine extract?" WITHOUT constructing an adapter -- the
#: console's preflight does, before an operator is charged for finding
#: out. Kept beside the code that enforces it: a rename there that
#: forgets this list is caught by `tests/ui/test_preflight.py`.
REQUIRED_ENV_VARS: tuple[str, ...] = (
    "IDP_CLIENT_ID",
    "IDP_CLIENT_SECRET",
    "IDP_REGION",
)
