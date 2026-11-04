#!/usr/bin/env python3
"""
SereinOS Core - Main Entry Point
"""
import asyncio
import signal
import sys
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent))

from serein_core import SereinCore
from serein_core.config import load_config


async def main():
    """Main async entry point for serein-core service"""
    
    # Load configuration
    config = load_config()
    
    # Create core instance
    core = SereinCore(config)
    
    # Setup signal handlers
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        print("\nShutdown signal received...")
        shutdown_event.set()
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    # Initialize core
    await core.initialize()
    
    print("SereinOS Core started successfully")
    print("Press Ctrl+C to stop")
    
    # Wait for shutdown
    await shutdown_event.wait()
    
    # Graceful shutdown
    print("Shutting down...")
    await core.shutdown()
    print("Shutdown complete")


def sync_main():
    """Synchronous entry point for console script"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    sync_main()