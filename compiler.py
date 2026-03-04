import sys
import json
import os
import subprocess
import re
from lark import Lark, Transformer
from openai import OpenAI

# ==========================================
# THE FRONTEND (Grammar)
# ==========================================
grammar = """
start: block+

?block: agent_def | prompt_def | pipeline

// --- Agent Rules ---
agent_def: "[Agent" "::" CNAME "]" directive*
directive: "@" CNAME "->" value

// --- Prompt Rules ---
prompt_def: "[Prompt" "::" CNAME "]" prompt_line*
prompt_line: ">>" (STRING | variable)+
variable: "$" CNAME

// --- Pipeline Rules ---
pipeline: CNAME ("|>" step)+

?step: CNAME | match_gate

// The Match Gate syntax: ?{ "condition" -> TargetAgent }
match_gate: "?{" match_branch+ "}"
match_branch: STRING "->" CNAME ("|>" CNAME)*

// --- Shared Rules ---
value: STRING | SIGNED_NUMBER
COMMENT: /\/\/.*/
%ignore COMMENT

%import common.CNAME
%import common.ESCAPED_STRING -> STRING
%import common.SIGNED_NUMBER
%import common.WS
%ignore WS
"""

# ==========================================
# THE MIDDLE-END (AST Transformer)
# ==========================================
class AionTransformer(Transformer):
    def value(self, args):
        val = args[0].value
        if val.startswith('"'):
            return val.strip('"')
        return float(val) if '.' in val else int(val)

    def directive(self, args):
        key = args[0].value
        val = args[1]
        return (key, val)

    def agent_def(self, args):
        name = args[0].value
        directives = dict(args[1:])
        return {"type": "Agent", "name": name, "config": directives}

    # --- Prompt Logic ---
    def variable(self, args):
        # Tags this chunk of the AST as a dynamic variable
        return {"is_var": True, "name": args[0].value}

    def prompt_line(self, args):
        # A line is now a list of parts (either static strings or variables)
        parts = []
        for arg in args:
            if isinstance(arg, dict):
                parts.append(arg) # It's our variable dictionary
            else:
                parts.append({"is_var": False, "text": arg.value.strip('"')})
        return parts

    def prompt_def(self, args):
        name = args[0].value
        lines = args[1:] # This is now a list of lines, which contain lists of parts
        return {"type": "Prompt", "name": name, "lines": lines}
    
    # --- Branching Logic ---
    def match_branch(self, args):
        # Extracts the "CONDITION" and the TargetName
        condition = args[0].value.strip('"')
        target_steps = [arg.value for arg in args[1:]]
        return (condition, target_steps)

    def match_gate(self, args):
        # Packages all the branches into a routing dictionary
        return {"type": "MatchGate", "routes": dict(args)}

    def pipeline(self, args):
        # The pipeline is now a mix of strings (Agents) and dictionaries (MatchGates)
        flow = []
        for arg in args:
            if isinstance(arg, dict):
                flow.append(arg)
            else:
                flow.append(arg.value)
        return {"type": "Pipeline", "flow": flow}

    def start(self, args):
        return args

# ==========================================
# THE BACKEND (Runtime Engine)
# ==========================================
class AionRuntime:
    def __init__(self, ast: dict, cli_vars: dict):
        self.agents = ast.get("agents", {})
        self.prompts = ast.get("prompts", {})
        self.pipeline = ast.get("pipeline", [])
        self.cli_vars = cli_vars
        self.replay_step = "" # Will be overwritten by __main__
        
        # Colors
        self.CYAN = '\033[96m'
        self.GREEN = '\033[92m'
        self.RED = '\033[91m'
        self.MAGENTA = '\033[95m' # New color for Time Travel
        self.RESET = '\033[0m'
        
        # Create cache directory if it doesn't exist
        if not os.path.exists(".aion_cache"):
            os.makedirs(".aion_cache")

    def load(self):
        """Scans the AST and loads definitions into memory."""
        for node in self.ast:
            if node["type"] == "Agent":
                self.agents[node["name"]] = node
            elif node["type"] == "Prompt":
                self.prompts[node["name"]] = node
            elif node["type"] == "Pipeline":
                self.pipeline = node["flow"]
    
    def _build_replay_queue(self, target_step, current_queue):
        """Deep-searches the DAG to build a queue starting from the target step."""
        for i, step in enumerate(current_queue):
            # 1. Is it in the main pipeline?
            if step == target_step:
                return current_queue[i:]
            
            # 2. Is it hiding inside a MatchGate branch?
            if isinstance(step, dict) and step.get("type") == "MatchGate":
                for condition, sub_pipeline in step["routes"].items():
                    if target_step in sub_pipeline:
                        idx = sub_pipeline.index(target_step)
                        return sub_pipeline[idx:]
        
        return None # Step not found anywhere

    def execute(self):
        """Flows data through the defined pipeline."""
        current_payload = ""
        active_system_prompt = ""

        print(f"\n{self.CYAN}[SYS] Aion Runtime Environment Initialized...{self.RESET}")

        # --- The Dynamic Execution Queue ---
        execution_queue = self.pipeline.copy()

        # --- THE REPLAY INJECTION ---
        if self.replay_step:
            new_queue = self._build_replay_queue(self.replay_step, execution_queue)
            
            if new_queue is not None:
                print(f"{self.MAGENTA}[SYS] TIME TRAVEL INITIATED: Fast-forwarding to {self.replay_step}...{self.RESET}")
                
                # Overwrite the execution queue with the newly sliced sub-pipeline
                execution_queue = new_queue
                
                # Load the state that was fed into this step last time
                cache_file = f".aion_cache/{self.replay_step}_input.json"
                if os.path.exists(cache_file):
                    with open(cache_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                        current_payload = state.get("payload", "")
                    print(f"{self.MAGENTA}[SYS] State restored successfully. Resuming execution.{self.RESET}\n")
                else:
                    print(f"{self.RED}[WARNING] No cache found for {self.replay_step}. Proceeding with empty payload.{self.RESET}\n")
            else:
                print(f"{self.RED}[FATAL] Replay step '{self.replay_step}' not found in pipeline or branches.{self.RESET}")
                return

        while execution_queue:
            step = execution_queue.pop(0)

            # --- THE STATE SNAPSHOT ---
            # Save the exact payload right before we process this step
            if isinstance(step, str):
                with open(f".aion_cache/{step}_input.json", "w", encoding="utf-8") as f:
                    json.dump({"payload": current_payload}, f)

            # 1. The Match Gate (Conditional Branching) MUST BE FIRST
            if isinstance(step, dict) and step.get("type") == "MatchGate":
                print(f"{self.CYAN}[SYS] MatchGate Engaged. Evaluating payload...{self.RESET}")
                
                routed = False
                for condition, target_steps in step["routes"].items():
                    if condition in current_payload:
                        route_path = " |> ".join(target_steps)
                        print(f"{self.GREEN}[SYS] Match found for '{condition}'. Routing -> {route_path}{self.RESET}")

                        execution_queue = target_steps + execution_queue 
                        routed = True
                        break
                
                if not routed:
                    print(f"{self.RED}[SYS] MatchGate hit a dead end (no conditions met). Halting execution.{self.RESET}")
                    break
                continue 

            # 2. Input
            elif step == "UserStream":
                current_payload = input(f"{self.GREEN}UserStream > {self.RESET}")
            
            # 3. Prompts
            elif step in self.prompts:
                raw_lines = self.prompts[step]["lines"]
                resolved_text = ""
                
                for line in raw_lines:
                    for part in line:
                        if part["is_var"]:
                            var_name = part["name"]
                            resolved_text += self.cli_vars.get(var_name, f"[MISSING VAR: {var_name}]")
                        else:
                            resolved_text += part["text"]
                    resolved_text += "\n"
                
                active_system_prompt = resolved_text.strip()
                print(f"{self.CYAN}[SYS] Attached Context: {step}{self.RESET}")
            
            # 4. Agents
            elif step in self.agents:
                agent = self.agents[step]
                model_name = agent["config"].get("model", "llama-3.1-8b-instant") 
                temp = float(agent["config"].get("temp", 0.7))
                
                print(f"{self.CYAN}[SYS] Establishing uplink to {model_name}...{self.RESET}")
                
                # Default to Groq if no base URL is provided, but allow overrides for local/OpenAI
                base_url = os.environ.get("AION_BASE_URL", "https://api.groq.com/openai/v1")
                api_key = os.environ.get("AION_API_KEY")
                
                # Local models (like Ollama) don't need real API keys, but remote ones do
                is_local = "localhost" in base_url or "127.0.0.1" in base_url
                
                if not api_key and not is_local:
                    print(f"{self.RED}[FATAL] AION_API_KEY environment variable is missing.{self.RESET}")
                    print(f"{self.CYAN}[SYS] Please set it in your terminal:{self.RESET}")
                    print(f"{self.CYAN}      $env:AION_API_KEY='your_key'{self.RESET}")
                    print(f"{self.CYAN}[SYS] (Optional) Change provider by setting AION_BASE_URL.{self.RESET}")
                    break
                
                # If local, provide a dummy key so the OpenAI library doesn't crash
                safe_key = api_key if api_key else "sk-local-dummy"
                
                client = OpenAI(
                    api_key=safe_key, 
                    base_url=base_url
                )
                
                messages = []
                if active_system_prompt:
                    messages.append({"role": "system", "content": active_system_prompt})
                messages.append({"role": "user", "content": current_payload})
                
                try:
                    response = client.chat.completions.create(
                        model=model_name,
                        temperature=temp,
                        messages=messages
                    )
                    raw_content = response.choices[0].message.content
                    current_payload = raw_content if raw_content is not None else "[APIT RETURNED EMPTY]"
                except Exception as e:
                    print(f"{self.RED}[FATAL] API Connection Severed: {e}{self.RESET}")
                    break
            
            # 5. Outputs
            elif step == "TerminalOut":
                print(f"\n{self.GREEN}>> {current_payload}{self.RESET}\n")

            # 6. The Actuator (SystemShell)
            elif step == "SystemShell":
                print(f"{self.CYAN}[SYS] SystemShell Engaged. Extracting code...{self.RESET}")
                
                match = re.search(r'```python\n(.*?)\n```', current_payload, re.DOTALL)
                
                if match:
                    code = match.group(1)
                    
                    print(f"\n{self.RED}=== WARNING: PENDING SYSTEM EXECUTION ==={self.RESET}")
                    print(f"{self.CYAN}{code}{self.RESET}")
                    print(f"{self.RED}========================================={self.RESET}")
                    
                    auth = input(f"{self.RED}[SYS] Do you authorize this code to run? (y/n): {self.RESET}").strip().lower()
                    
                    if auth == 'y' or auth == 'yes':
                        with open("aion_temp_exec.py", "w", encoding="utf-8") as f:
                            f.write(code)
                        
                        try:
                            print(f"{self.CYAN}[SYS] Executing payload on local machine...{self.RESET}")
                            result = subprocess.run(
                                ["python", "aion_temp_exec.py"], 
                                capture_output=True, text=True, timeout=15
                            )
                            
                            if result.returncode == 0:
                                current_payload = f"[EXECUTION SUCCESS]\n{result.stdout}"
                            else:
                                current_payload = f"[EXECUTION FAILED - TRACEBACK]\n{result.stderr}"
                                
                        except Exception as e:
                            current_payload = f"[CRITICAL SYSTEM ERROR] {e}"
                    else:
                        print(f"{self.CYAN}[SYS] Execution aborted by user.{self.RESET}")
                        current_payload = "[EXECUTION ABORTED BY USER]"
                else:
                    current_payload = "[ERROR] No valid Python code block found in the payload."

            # 7. File Output Operator
            elif step == "FileOut":
                try:
                    with open("agent_output.md", "w", encoding="utf-8") as f:
                        f.write(current_payload)
                    print(f"{self.CYAN}[SYS] Payload successfully written to agent_output.md{self.RESET}")
                except Exception as e:
                    print(f"{self.RED}[FATAL] Failed to write file: {e}{self.RESET}")
            
            # 8. Fallback
            else:
                print(f"{self.RED}[FATAL] Unknown Pipeline Step: {step}{self.RESET}")
                break

# ==========================================
# EXECUTION TRIGGER (The CLI)
# ==========================================
if __name__ == "__main__":
    # Check if the user provided a file
    if len(sys.argv) < 2:
        print("Usage: python compiler.py <file.aion> [--var_name value] [--replay StepName]")
        sys.exit(1)

    filename = sys.argv[1]

    # Extract variables
    cli_vars = {}
    replay_step = ""

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--replay" and i + 1 < len(sys.argv):
            replay_step = sys.argv[i + 1]
            i += 2
        elif sys.argv[i].startswith("--") and i + 1 < len(sys.argv):
            cli_vars[sys.argv[i][2:]] = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    with open(filename, "r", encoding="utf-8") as f:
        code = f.read()

    parser = Lark(grammar, start='start', parser='lalr')
    tree = parser.parse(code)

    transfomer = AionTransformer()
    raw_ast = transfomer.transform(tree)

    ast_dict = {"agents": {}, "prompts": {}, "pipeline": []}
    for item in raw_ast:
        if item["type"] == "Agent":
            ast_dict["agents"][item["name"]] = item
        elif item["type"] == "Prompt":
            ast_dict["prompts"][item["name"]] = item
        elif item["type"] == "Pipeline":
            ast_dict["pipeline"] = item["flow"]

    # Pass the replay step into the runtime
    runtime = AionRuntime(ast_dict, cli_vars)
    runtime.replay_step = replay_step
    runtime.execute()