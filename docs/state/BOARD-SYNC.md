# Board sync — pending reconciliation

Board operations that could NOT be applied to Trello because the board was unreachable
(Composio didn't register that session), recorded so nothing is lost and the divergence
is visible — never a silent surprise. When the board is back, Dunga offers to replay these
(at session start, or via `/standup`), then clears the applied lines.

Only used when a board is EXPECTED (`## Issue tracker: Board: Trello`). In declared
boardless mode (`Board: none`) this file stays empty — there's no board to fall behind.

| Date | Op (create/move) | Card / story | Details | Status (pending/applied) |
|------|------------------|--------------|---------|--------------------------|
