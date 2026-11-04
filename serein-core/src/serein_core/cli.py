"""
CLI for SereinOS Core Service - administration and testing.
"""
import asyncio
import json
import sys
from pathlib import Path
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.json import JSON

from serein_core.config import CoreConfig, get_config as config
from serein_core.database import Database, get_database
from serein_core.identity import IdentityEngine
from serein_core.persona import persona_engine
from serein_core.emotion import emotion_engine
from serein_core.memory import memory_engine
from serein_core.relationship import relationship_engine
from serein_core.growth import growth_engine
from serein_core.ai import get_router, ModelTier
from serein_core.command_bus import get_command_bus
from serein_core.scheduler import get_scheduler, TriggerType

console = Console()


@click.group()
@click.option('--config', '-c', type=click.Path(exists=True), help='Config file path')
@click.pass_context
def cli(ctx, config_path):
    """SereinOS Core Service CLI."""
    ctx.ensure_object(dict)
    if config_path:
        ctx.obj['config'] = CoreConfig.from_yaml(Path(config_path))


@cli.command()
@click.pass_context
def init(ctx):
    """Initialize database and create default config."""
    async def _init():
        if config_path := ctx.obj.get('config'):
            cfg = config_path
        else:
            cfg = config
        
        console.print("[bold green]Initializing SereinOS Core...[/bold green]")
        
        # Create directories
        cfg.database.path.parent.mkdir(parents=True, exist_ok=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.config_dir.mkdir(parents=True, exist_ok=True)
        cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
        
        # Save default config
        cfg.to_yaml(cfg.config_dir / "config.yaml")
        console.print(f"[OK] Config saved to {cfg.config_dir / 'config.yaml'}")
        
        # Initialize database
        db = Database(cfg.database.path, cfg.database.encryption_key, cfg.database.pragmas)
        await db.connect()
        console.print("[OK] Database initialized")
        
        console.print("[bold green]Initialization complete![/bold green]")
    
    asyncio.run(_init())


@cli.command()
@click.pass_context
def status(ctx):
    """Show service status."""
    async def _status():
        try:
            db = await get_database()
            
            # Identity
            ident = await db.get_identity()
            if ident:
                console.print(Panel(f"User: {ident['user_name']}\nPreferred: {ident['preferred_name'] or 'Not set'}\nTimezone: {ident['timezone']}", title="Identity"))
            
            # Personality
            pers = await db.get_personality()
            if pers:
                table = Table(title="Personality")
                table.add_column("Trait")
                table.add_column("Value")
                for k, v in pers.items():
                    if k not in ('id', 'updated_at'):
                        table.add_row(k.title(), str(v))
                console.print(table)
            
            # Emotions
            emo = await db.get_emotions()
            if emo:
                table = Table(title="Emotions")
                table.add_column("Emotion")
                table.add_column("Level")
                for k, v in emo.items():
                    if k not in ('id', 'updated_at'):
                        bar = "█" * (v // 5) + "░" * (20 - v // 5)
                        table.add_row(k.title(), f"{bar} {v}")
                console.print(table)
            
            # Relationship
            rel = await db.get_relationship()
            if rel:
                rel_engine = relationship_engine
                await rel_engine.initialize()
                console.print(Panel(rel_engine.get_context(), title="Relationship"))
            
            # Growth
            growth = await db.get_growth()
            if growth:
                gr_engine = growth_engine
                await gr_engine.initialize()
                console.print(Panel(gr_engine.get_context(), title="Growth"))
            
            # Memory stats
            mem_stats = await memory_engine.get_stats()
            console.print(Panel(JSON(json.dumps(mem_stats, indent=2)), title="Memory Stats"))
            
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
    
    asyncio.run(_status())


@cli.command()
@click.argument('message')
@click.option('--tier', type=click.Choice(['fast', 'smart', 'local']), default='fast')
@click.pass_context
def chat(ctx, message, tier):
    """Test chat with AI router."""
    async def _chat():
        router = await get_router()
        from .ai import ModelTier
        
        response = await router.chat(
            [{"role": "user", "content": message}],
            tier=ModelTier(tier),
        )
        
        console.print(Panel(response.content, title=f"Response ({response.provider}/{response.model}, {response.latency_ms}ms)"))
    
    asyncio.run(_chat())


@cli.command()
@click.argument('command')
@click.argument('params', default='{}')
@click.pass_context
def exec_cmd(ctx, command, params):
    """Execute a Command Bus command."""
    async def _exec():
        bus = await get_command_bus()
        import json
        params_dict = json.loads(params)
        result = await bus.execute(command, params_dict)
        console.print(JSON(json.dumps({
            "success": result.success,
            "result": result.result,
            "error": result.error,
            "tier": result.permission_tier.value,
            "requires_confirmation": result.requires_confirmation,
        }, indent=2)))
    
    asyncio.run(_exec())


@cli.command()
@click.argument('message')
@click.option('--in-minutes', type=int)
@click.option('--at', type=str, help='ISO format datetime')
@click.option('--cron', type=str)
@click.pass_context
def remind(ctx, message, in_minutes, at, cron):
    """Schedule a reminder."""
    async def _remind():
        sched = await get_scheduler()
        from datetime import datetime, timedelta
        
        if in_minutes:
            event_id = await sched.remind_in(message, timedelta(minutes=in_minutes))
        elif at:
            event_id = await sched.remind_at(message, datetime.fromisoformat(at))
        elif cron:
            event_id = await sched.remind_recurring(message, cron)
        else:
            console.print("[red]Specify --in-minutes, --at, or --cron[/red]")
            return
        
        console.print(f"[green]Reminder scheduled: {event_id}[/green]")
    
    asyncio.run(_remind())


@cli.command()
@click.pass_context
def events(ctx):
    """List scheduled events."""
    async def _events():
        sched = await get_scheduler()
        events = await sched.get_pending_events()
        
        table = Table(title="Scheduled Events")
        table.add_column("ID")
        table.add_column("Type")
        table.add_column("Fires At")
        table.add_column("Message")
        table.add_column("Status")
        
        for e in events:
            table.add_row(
                e.id[:8],
                e.trigger_type.value,
                e.fires_at.isoformat() if e.fires_at else e.recurrence_rule,
                e.message[:50],
                e.status,
            )
        
        console.print(table)
    
    asyncio.run(_events())


@cli.command()
@click.pass_context
def test_db(ctx):
    """Test database operations."""
    async def _test():
        db = await get_database()
        
        # Test memory
        mem_id = await db.save_memory({
            "content": "Test memory from CLI",
            "tags": ["test", "cli"],
            "importance": 75,
            "emotion": "neutral",
            "source": "cli",
            "memory_type": "semantic",
        })
        console.print(f"[green]Saved memory: {mem_id}[/green]")
        
        # Search
        results = await db.search_memories("test", limit=5)
        console.print(f"[green]Found {len(results)} memories[/green]")
        
        # Test scheduled event
        from datetime import datetime, timedelta
        evt_id = await db.save_scheduled_event({
            "trigger_type": "relative",
            "fires_at": (datetime.now() + timedelta(minutes=5)).isoformat(),
            "message": "Test reminder from CLI",
            "status": "pending",
        })
        console.print(f"[green]Scheduled event: {evt_id}[/green]")
        
        console.print("[bold green]Database tests passed![/bold green]")
    
    asyncio.run(_test())


if __name__ == "__main__":
    cli()