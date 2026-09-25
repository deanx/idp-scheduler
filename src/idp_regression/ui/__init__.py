"""Epic E — the local console.

A small React app plus a read-only FastAPI backend, for the three
questions the evaluation platform structurally cannot answer (noise
floor, gate blind spots, the pin store's action/version axis) and for
authoring custom scorers as data rather than code.

`reader.py` is the read side, `custom_scorers.py` the authoring side,
`uploads.py` the ZIP trust boundary, `api.py` the HTTP surface and
`server.py` the loopback-only entry point. Nothing in this package
spends IDP quota or writes to the evaluation platform.
"""
