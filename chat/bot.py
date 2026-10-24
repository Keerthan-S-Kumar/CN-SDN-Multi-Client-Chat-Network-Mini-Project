"""OWNER: A. Load generator (D2). CLI in docs/interfaces.md §6.
N bots register/login, join --room, send room messages at --rate msg/s for --duration s.
Each message text embeds a unique seq; receivers log arrival. Writes CSV per §11:
  t_send,t_recv,sender,receiver,kind,room,bytes   (empty t_recv = lost)
Flag --spammer sends 200 msg/s. Bots only talk through the real client protocol."""

if __name__ == "__main__":
    raise SystemExit("TODO (A): implement bots")
