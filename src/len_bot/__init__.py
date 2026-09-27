"""len_bot package. Importing the library does not start the runtime."""

def main() -> None:
    from len_bot.next.host import main as run
    run()
