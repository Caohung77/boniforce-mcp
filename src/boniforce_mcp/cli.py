"""Admin CLI for the Boniforce MCP server."""
from __future__ import annotations

import asyncio
import getpass

import typer

from . import auth, crypto, storage

app = typer.Typer(help="Boniforce MCP admin commands.")


def _run(coro):
    return asyncio.run(coro)


@app.command()
def genkey() -> None:
    """Generate a Fernet key for BF_ENCRYPTION_KEY."""
    typer.echo(crypto.generate_key())


@app.command()
def gensigning() -> None:
    """Generate an RSA private key (PEM) for BF_OAUTH_SIGNING_KEY."""
    typer.echo(auth.generate_signing_key_pem())


@app.command()
def initdb() -> None:
    """Create the SQLite schema (idempotent)."""
    _run(storage.init_db())
    typer.echo("Database initialized.")


@app.command()
def adduser(email: str) -> None:
    """Create an MCP user. Prompts for password."""
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm:  ")
    if pw1 != pw2:
        typer.echo("Passwords do not match.", err=True)
        raise typer.Exit(1)
    if len(pw1) < 8:
        typer.echo("Password must be at least 8 characters.", err=True)
        raise typer.Exit(1)
    _run(storage.init_db())
    user = _run(storage.create_user(email, pw1))
    typer.echo(f"Created user {user.email} (id {user.id}).")


@app.command()
def setkey(email: str, label: str | None = None) -> None:
    """Store a Boniforce API token for the given user (encrypted)."""
    user = _run(storage.get_user_by_email(email))
    if not user:
        typer.echo(f"No such user: {email}", err=True)
        raise typer.Exit(1)
    token = getpass.getpass("Boniforce token: ").strip()
    if not token:
        typer.echo("Empty token.", err=True)
        raise typer.Exit(1)
    _run(storage.set_bf_token(user.id, token, label))
    typer.echo(f"Boniforce token saved for {email}.")


@app.command()
def listusers() -> None:
    """List all users."""
    users = _run(storage.list_users())
    if not users:
        typer.echo("(no users)")
        return
    for u in users:
        typer.echo(f"{u.id}\t{u.email}")


@app.command("register-gpt-client")
def register_gpt_client(
    name: str = typer.Option(..., help="Human label, e.g. 'ChatGPT Boniforce GPT'."),
    redirect_uri: str = typer.Option(
        None,
        help="Specific OAuth callback URL. Omit when --chatgpt is set.",
    ),
    chatgpt: bool = typer.Option(
        False,
        "--chatgpt",
        help=(
            "Register a wildcard client that accepts any ChatGPT Custom-GPT "
            "callback (https://chat.openai.com/aip/*/oauth/callback and the "
            "chatgpt.com variant). Avoids re-registering every time the GPT "
            "draft id changes."
        ),
    ),
) -> None:
    """Register a static OAuth client for ChatGPT Custom GPT Actions.

    Custom GPT Actions don't support Dynamic Client Registration, so we
    pre-register a confidential client and print client_id + client_secret
    to paste into the GPT builder's OAuth fields.
    """
    if chatgpt:
        redirect_uris = [
            "https://chat.openai.com/aip/*/oauth/callback",
            "https://chatgpt.com/aip/*/oauth/callback",
        ]
    elif redirect_uri:
        redirect_uris = [redirect_uri]
    else:
        typer.echo("Provide --redirect-uri or --chatgpt.", err=True)
        raise typer.Exit(2)
    _run(storage.init_db())
    client_id, secret = _run(
        storage.register_client(name, redirect_uris, "client_secret_post")
    )
    typer.echo(f"client_id:     {client_id}")
    typer.echo(f"client_secret: {secret}")
    typer.echo("")
    typer.echo("Paste these into the Custom GPT 'Aktionen' OAuth panel.")
    typer.echo("Save the client_secret now — it cannot be recovered.")


if __name__ == "__main__":
    app()
