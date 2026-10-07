# Connect Kiro to the ChemBioCatalyst SAR Workbench

This guide gets Kiro talking to the shared SAR Workbench on ChemBioCatalyst, so you can ask questions about your projects in plain language ("which compounds in project X have IC50 under 100 nM?"). It takes about 10 minutes and you only do it once.

Kiro runs a small helper program on your computer (the "MCP server"). The helper signs in to the SAR Workbench **as you**, using a personal token, so you see exactly the projects you are a member of and nothing else.

> **Where is the server?** Your SAR Workbench address is shown on the **API tokens** page (step 3). Below it is written as `<SERVER>`, for example `https://host:8029`. Use the address from that page.

## Before you start

- [ ] A portal administrator has given you access to **SAR Workbench** (you can see its card on the ChemBioCatalyst portal).
- [ ] You are on the network that can reach the portal (on site or VPN).
- [ ] Kiro is installed.
- [ ] `git` and `python3` work in a terminal. Check with `git --version` and `python3 --version` (any recent Python 3 is fine; nothing needs installing).
- [ ] You use macOS or Linux. Windows has not been tested and is not supported by this guide.

## Step 1. Get the helper program

In a terminal:

```bash
git clone --branch chembiocatalyst https://github.com/yipy0005/SAR_tool.git ~/sar-workbench-mcp
cd ~/sar-workbench-mcp
```

Every command below is run from this folder. (If you opened a new terminal, run `cd ~/sar-workbench-mcp` again.)

## Step 2. Save the server certificate

The ChemBioCatalyst servers use their own security certificate, which your computer does not know yet. The helper needs a copy of the **public** certificate so it can check it is talking to the real server.

1. Open SAR Workbench from the portal and click **API tokens** in the page header. (You may need to accept your browser's certificate warning first, exactly as you do for the portal.)
2. Click **Download the server certificate**.
3. Move it into place:

```bash
mkdir -p ~/.config/sar-workbench
mv ~/Downloads/chembiocatalyst.crt ~/.config/sar-workbench/chembiocatalyst.crt
```

If there is no download button, ask a platform administrator for the file `chembiocatalyst.crt`.

The certificate is only a public identity card for the server, not a secret. It is used by the helper alone, so you do **not** need to install it into macOS, Linux or your browser.

## Step 3. Create your token

On the same **API tokens** page:

1. Type a name you will recognise, such as `Kiro on my laptop`.
2. Choose how many days it should last (30 by default, up to 90).
3. Click **Create token**.
4. **Copy the token now.** It starts with `sarpat_` and is shown only once. If you lose it, create another.

A token works like a password. Anyone who has it can act as you, so do not paste it into chats, emails or tickets.

## Step 4. Save the token on your computer

```bash
python3 sar_mcp_server.py --save-token
```

Paste the token when asked (nothing appears on screen as you paste, which is normal) and press Enter. It is stored in `~/.config/sar-workbench/mcp.env`, readable only by you, and is never written into Kiro's settings file.

## Step 5. Add it to Kiro

Print the settings entry for Kiro. Replace `<SERVER>` with your address from the API tokens page:

```bash
python3 sar_mcp_server.py --print-kiro-config \
  --base-url <SERVER> \
  --ca-bundle ~/.config/sar-workbench/chembiocatalyst.crt
```

It prints something like this (your paths will differ):

```json
{
  "mcpServers": {
    "sar-workbench": {
      "command": "/usr/bin/python3",
      "args": ["/Users/you/sar-workbench-mcp/sar_mcp_server.py"],
      "env": {
        "SAR_MCP_BASE_URL": "<SERVER>",
        "SAR_MCP_MODE": "analyze",
        "SAR_MCP_CA_BUNDLE": "/Users/you/.config/sar-workbench/chembiocatalyst.crt"
      },
      "autoApprove": ["sar_status", "sar_list_projects", "..."],
      "disabled": false
    }
  }
}
```

Now put that into Kiro's settings file, `~/.kiro/settings/mcp.json`:

- **The file does not exist yet:** create it and paste the whole output.
- **The file already exists:** keep what is there and add only the `"sar-workbench": { ... }` block inside the existing `"mcpServers": { ... }`, with a comma between entries.

Kiro reconnects by itself when the file changes. If it does not, open Kiro's **MCP Server** view and reconnect `sar-workbench`.

## Step 6. Check that it works

First from the terminal:

```bash
SAR_MCP_BASE_URL=<SERVER> \
SAR_MCP_CA_BUNDLE=~/.config/sar-workbench/chembiocatalyst.crt \
python3 sar_mcp_server.py --check
```

A good result ends with `OK` and shows your email address. Then ask Kiro:

> Use the sar-workbench tools to check the connection, then list my projects.

You should see your email and your projects.

## Using it

Ask in plain language. Kiro picks the tools. Most answers include a link that opens the matching page of the web app so you can look at the result yourself.

The default mode, `analyze`, lets the assistant read data and run analyses that are stored in the web app (they appear there for everyone on the project). To make it read-only, change `"SAR_MCP_MODE": "analyze"` to `"read-only"` in `mcp.json`. The assistant can never approve recommendations, change project members or delete data. See [MCP_GUIDE.md](MCP_GUIDE.md) for the full tool list.

## Keeping things safe

- **Your data goes to the AI provider.** What the assistant reads is sent to the model behind Kiro. Connect only projects you are allowed to share that way. To fence the assistant to chosen projects, add `"SAR_MCP_PROJECT_IDS": "id1,id2"` to the `env` block in `mcp.json`.
- **Lost a laptop or shared a token by mistake?** Open the API tokens page and click **Revoke**. It stops working immediately.
- **Tokens expire.** When yours does, Kiro will say so. Create a new one (step 3) and run step 4 again. Nothing else changes.
- **If you leave the project or lose portal access,** your token stops working within about five minutes.

## Updating the helper

```bash
cd ~/sar-workbench-mcp
git pull
```

## Troubleshooting

| You see | What it means | What to do |
| --- | --- | --- |
| `CERTIFICATE_VERIFY_FAILED` | The helper does not trust the server's certificate | Redo step 2. Check the `--ca-bundle` path exists and is the same one in `mcp.json` |
| `Cannot reach the SAR Workbench` | You are off the network, or `<SERVER>` is wrong | Connect to the VPN. Copy the address again from the API tokens page |
| `rejected the API token` | Token expired, was revoked, or was mistyped | Create a new token (step 3), then step 4 |
| `no longer has access` | Your portal access to SAR Workbench was removed | Ask a portal administrator |
| `can be read by other users` | Wrong file permissions on the token file | `chmod 600 ~/.config/sar-workbench/mcp.env` |
| `does not look like a SAR Workbench API token` | The paste was cut off or has extra text | Copy the full token starting with `sarpat_` |
| The tools do not appear in Kiro | Kiro has not loaded the entry | Check `mcp.json` is valid JSON, then reconnect in the MCP Server view |
| Your projects list is empty | You are not a member of any project yet | Ask a project owner to add you |
| A tool says `403` | You lack the role it needs (analyses need editor access) | Ask the project owner to change your role |

## Removing it

Delete the `sar-workbench` block from `~/.kiro/settings/mcp.json`, revoke your token on the API tokens page, then delete `~/.config/sar-workbench/` and `~/sar-workbench-mcp/`.
