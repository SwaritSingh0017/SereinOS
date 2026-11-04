import time
import sys
from src.serein_compat import SereinOS
from src.ui.ui_engine import ConsoleUI

print("\n" + "="*50)
print("SereinOS")
print("="*50)

# Initialize SereinOS
serein = SereinOS()
serein.initialize()

# Check if using console backend (to avoid double-print)
_using_console_backend = isinstance(serein.ui._backend, ConsoleUI) if serein.ui and serein.ui._backend else False

# Set up UI input callback
def on_ui_input(user_input: str):
    """Handle input from UI."""
    if user_input.lower() in ["exit", "quit", "bye"]:
        print("\nSerein: Goodbye for now. I'll be here when you need me.")
        serein.shutdown()
        if serein.ui:
            serein.ui.shutdown()
        sys.exit(0)
    
    if not user_input:
        return
    
    start = time.time()
    response = serein.process_input(user_input)
    end = time.time()
    
    # Send response to UI
    if serein.ui:
        serein.ui.append_message("serein", response['content'])
    
    # Only print to console if NOT using ConsoleUI backend (to avoid double-print)
    if not _using_console_backend:
        print(f"\nSerein: {response['content']}")
        print(f"\n[Response Time: {end - start:.2f}s | Action: {response.get('action', 'respond')} | Provider: {response.get('provider', 'unknown')}]")

# Connect UI to input handler
if serein.ui:
    serein.ui.on_input(on_ui_input)
    
    # Boot sequence
    greeting = serein.get_greeting()
    serein.ui.boot(greeting)
    serein.ui.start_conversation()
    serein.ui.show()
    
    # Run UI event loop (blocking)
    serein.ui.run()
else:
    # Fallback to console mode
    print(f"\nSerein: {serein.get_greeting()}\n")
    
    while True:
        try:
            user_input = input("You: ").strip()
            
            if user_input.lower() in ["exit", "quit", "bye"]:
                print(f"\nSerein: Goodbye for now. I'll be here when you need me.")
                break
            
            if not user_input:
                continue
            
            start = time.time()
            response = serein.process_input(user_input)
            end = time.time()
            
            print(f"\nSerein: {response['content']}")
            print(f"\n[Response Time: {end - start:.2f}s | Action: {response.get('action', 'respond')} | Provider: {response.get('provider', 'unknown')}]")
            
        except KeyboardInterrupt:
            print("\n\nSerein: Interrupted. Shutting down...")
            break
        except Exception as e:
            print(f"\n[Error: {e}]")

serein.shutdown()