"""OWNER: A. CLI chat client + scriptable bot mode (used by eval in D2).

CLI: python3 chat/client.py --server 10.0.0.1 --user alice
Commands: /register /login /join <room> /leave <room> /list rooms|users /msg <room> <text> /pm <user> <text> /quit
Opens 5000 (control), then 5001 and 5002 after login using ATTACH (docs/interfaces.md §3).
"""

if __name__ == "__main__":
    raise SystemExit("TODO (A): implement client")
