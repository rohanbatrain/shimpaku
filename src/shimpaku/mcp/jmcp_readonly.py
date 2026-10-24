"""Launch Juniper's junos-mcp-server with its write tools unregistered.

Runs under the junos-mcp-server venv (mcp<2), not ours, so it imports nothing from shimpaku.

    <jmcp-venv>/bin/python jmcp_readonly.py <jmcp_dir> -f devices.json        (stdio only)
"""

import sys

READ_TOOLS = {"execute_junos_command", "execute_junos_command_batch", "get_junos_config",
              "junos_config_diff", "gather_device_facts", "get_router_list"}
# Note: get_junos_config is kept registered in Juniper's server, but shimpaku only calls
# execute_junos_command, after its own allowlist.


def main() -> None:
    jdir = sys.argv.pop(1)
    sys.path.insert(0, jdir)
    import jmcp  # noqa: PLC0415
    import mcp.types as types  # noqa: PLC0415

    for name in list(jmcp.TOOL_HANDLERS):
        if name not in READ_TOOLS:
            del jmcp.TOOL_HANDLERS[name]

    original = jmcp.create_mcp_server

    def create_ro():
        app = original()
        h = app.request_handlers[types.ListToolsRequest]

        async def filtered(req):
            res = await h(req)
            res.root.tools = [t for t in res.root.tools if t.name in READ_TOOLS]
            return res

        app.request_handlers[types.ListToolsRequest] = filtered
        return app

    jmcp.create_mcp_server = create_ro
    if "-t" not in sys.argv:
        sys.argv += ["-t", "stdio"]
    jmcp.main()


if __name__ == "__main__":
    main()
