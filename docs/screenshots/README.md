# Screenshots

Captured from the running system. Files in this folder are linked from the main README.

| File | Shows |
|---|---|
| `web-chat-signin.png` | Browser chat: sign-in page |
| `web-chat-room.png` | Browser chat: a room conversation |
| `web-chat-private-message.png` | Browser chat: unread private message badge |

To be added from a live Mininet run (deliverable 1 evidence): the dashboard before and after a user registers,
`ovs-ofctl dump-flows` before and after registration, the timed-out connection from an unregistered host,
and the passing test output. Deliverable 2: dashboard under congestion and link failure, and the result graphs.

## Redesigned UI (design previews, sample data)

`dashboard-v3-*.png` and `web-chat-v2-network-panel.png` show the redesigned dashboard and web chat. The chat server and
web gateway were real; the controller was replaced by a small data stub so every state (congestion, a throttled host,
a re-route) could be shown at once. They are previews of the layout, not measurements. Live screenshots from the
Mininet VM replace them before the final demo.

`web-chat-v2-file-sharing.png` shows a file shared in a room (real chat server and gateway, a 1.3 MB file uploaded in the
browser and downloaded by a second user, byte for byte). The throttled banner and the network panel numbers come from
the same data stub as above.
