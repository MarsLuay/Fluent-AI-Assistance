# Security Policy

## Reporting

Report vulnerabilities privately to the repository owner. Do not open a public
issue containing credentials, proprietary ZEIA content, instrument logs, or
details that could enable unsafe hardware operation.

Include reproduction steps, affected versions, and expected impact when
possible.

## Sensitive data

ZEIA exports, FluentControl logs, Snapshot archives, generated bundles, and
instrument configuration may contain proprietary or site-specific information.
They remain local by default and should not be committed or uploaded without
authorization.

## MCP boundary

The MCP server uses local stdio transport. It does not provide network
listeners, remote authentication, driver installation, FluentControl database
writes, UI automation, or hardware commands.
