APP_NAME = "raspi-manager"


def main() -> None:
    from raspi.cli.main import app

    app()
