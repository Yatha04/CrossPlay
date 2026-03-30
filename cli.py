import sys
import click
from rich.console import Console
from rich.panel import Panel

from config import load_config
from main import main as run_daemon

console = Console()

@click.group()
def cli():
    """CrossPlay CLI — sync and migrate playlists between Spotify and YouTube Music."""
    pass

from auth.spotify_auth import build_auth_manager, store_spotify_token
from auth.youtube_auth import store_youtube_token
from db.queries import get_auth_token
import os
import json

@cli.command()
def auth():
    """Interactive authentication wizard for Spotify & YouTube Music."""
    console.print(Panel("[bold green]Authentication Wizard[/bold green]\n\nLet's get your accounts connected!"))
    
    cfg = load_config()
    db_path = cfg.database_path

    # Check Spotify
    sp_token = get_auth_token(db_path, "spotify", "user_b")
    if sp_token:
        console.print("[green]✓ Spotify is already connected.[/green]")
    else:
        console.print("[yellow]! Spotify is not connected.[/yellow]")
        if click.confirm("Connect Spotify now?"):
            auth_manager = build_auth_manager(cfg.spotify_client_id, cfg.spotify_redirect_uri)
            auth_url = auth_manager.get_authorize_url()
            console.print(f"\n[bold]1.[/bold] Go to this URL in your browser: [cyan]{auth_url}[/cyan]")
            console.print("[bold]2.[/bold] Accept the permissions.")
            console.print("[bold]3.[/bold] You will be redirected to a localhost URL. Copy the ENTIRE URL you are redirected to.")
            redirected_url = click.prompt("Paste the redirected URL here")
            
            try:
                code = auth_manager.parse_response_code(redirected_url)
                token_info = auth_manager.get_access_token(code)
                store_spotify_token(db_path, "user_b", token_info, cfg.spotify_playlist_id)
                console.print("[green]✓ Successfully connected Spotify![/green]\n")
            except Exception as e:
                console.print(f"[red]Failed to connect Spotify:[/red] {e}\n")

    # Check YouTube Music
    yt_token = get_auth_token(db_path, "youtube_music", "user_a")
    if yt_token:
        console.print("[green]✓ YouTube Music is already connected.[/green]")
    else:
        console.print("[yellow]! YouTube Music is not connected.[/yellow]")
        if click.confirm("Connect YouTube Music now?"):
            console.print("Running YouTube Music OAuth. Follow the prompts in your browser...")
            # Run ytmusicapi oauth
            os.system("python -m ytmusicapi oauth")
            
            if os.path.exists("oauth.json"):
                with open("oauth.json", "r") as f:
                    oauth_json_str = f.read()
                store_youtube_token(db_path, "user_a", oauth_json_str, cfg.youtube_playlist_id)
                console.print("[green]✓ Successfully connected YouTube Music![/green]\n")
            else:
                console.print("[red]Failed to find oauth.json. YouTube Music authentication failed.[/red]\n")

from migrate.migrator import run_migration_async
from migrate.fetcher import parse_playlist_url
from auth.spotify_auth import get_spotify_client
from auth.youtube_auth import get_ytmusic_client
from db.queries import get_migration_job
import time
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

@cli.command()
@click.argument('url')
@click.option('--to', type=click.Choice(['spotify', 'youtube_music']), required=True, help="Target platform to migrate to.")
def migrate(url, to):
    """Migrate a public playlist to your account with a live progress bar."""
    cfg = load_config()
    db_path = cfg.database_path

    sp = get_spotify_client(cfg.spotify_client_id, cfg.spotify_redirect_uri, db_path)
    yt = get_ytmusic_client(db_path, yt_oauth_json_b64=cfg.yt_oauth_json)

    if to == "spotify" and not sp:
        console.print("[red]Spotify not authenticated. Run 'python cli.py auth' first.[/red]")
        return
    if to == "youtube_music" and not yt:
        console.print("[red]YouTube Music not authenticated. Run 'python cli.py auth' first.[/red]")
        return
        
    console.print(f"Preparing to migrate to [bold green]{to}[/bold green]...")

    try:
        job_id = run_migration_async(
            source_url=url,
            target_platform=to,
            sp=sp,
            yt=yt,
            db_path=db_path,
            client_id=cfg.spotify_client_id,
            client_secret=cfg.spotify_client_secret,
            fuzzy_threshold=cfg.fuzzy_match_threshold,
        )
    except ValueError as e:
        console.print(f"[red]Error starting migration:[/red] {e}")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Initializing...", total=None)
        
        while True:
            time.sleep(0.5)
            job = get_migration_job(db_path, job_id)
            if not job:
                continue
                
            total = job['total_tracks']
            processed = job['matched_tracks'] + job['failed_tracks']
            
            if total > 0:
                progress.update(task, total=total, completed=processed, description=f"[cyan]Migrating '{job['source_playlist_name']}'...")
            
            if job['status'] == 'completed':
                progress.update(task, completed=total, description="[green]Migration complete![/green]")
                break
            elif job['status'] == 'failed':
                progress.update(task, description="[red]Migration failed![/red]")
                break
                
    # Final recap
    final_job = get_migration_job(db_path, job_id)
    console.print(f"\n[bold]Migration Recap:[/bold]")
    console.print(f"Total Tracks: {final_job['total_tracks']}")
    console.print(f"[green]Matched:[/green] {final_job['matched_tracks']}")
    console.print(f"[red]Failed:[/red] {final_job['failed_tracks']}")

from sync.engine import run_sync_cycle

@cli.command()
def sync():
    """Perform a one-off sync between configured playlists."""
    cfg = load_config()
    db_path = cfg.database_path

    sp = get_spotify_client(cfg.spotify_client_id, cfg.spotify_redirect_uri, db_path)
    yt = get_ytmusic_client(db_path, yt_oauth_json_b64=cfg.yt_oauth_json)

    if not sp:
        console.print("[red]Spotify not authenticated. Run 'python cli.py auth' first.[/red]")
        return
    if not yt:
        console.print("[red]YouTube Music not authenticated. Run 'python cli.py auth' first.[/red]")
        return
        
    console.print("[bold green]Running one-off sync...[/bold green]")
    
    try:
        run_sync_cycle(
            sp, yt,
            cfg.spotify_playlist_id,
            cfg.youtube_playlist_id,
            cfg.database_path,
            cfg.fuzzy_match_threshold,
        )
        console.print("[bold green]Sync cycle completed successfully![/bold green]")
    except Exception as e:
        console.print(f"[bold red]Sync cycle failed:[/bold red] {e}")

@cli.command()
def daemon():
    """Start the background sync daemon and API server."""
    console.print("[bold green]Starting daemon...[/bold green]")
    run_daemon()

if __name__ == '__main__':
    try:
        cfg = load_config()
    except Exception as e:
        console.print(f"[bold red]Failed to load config:[/bold red] {e}")
        sys.exit(1)
    cli()
