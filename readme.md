# Aion Compiler & DAG Orchestrator

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/endoftheend)

Aion is a lightweight, model-agnostic, multi-agent AI framework built entirely from scratch in Python. It features a bespoke programming language (`.aion`), a Directed Acyclic Graph (DAG) execution engine, and native time-travel debugging.

## Features
* **Custom Syntax:** Define agents, system prompts, and execution rules in a clean, readable format.
* **DAG Routing:** Dynamically route outputs to different AI agents based on regex pattern matching (`MatchGate`).
* **Time-Travel Debugging:** Instantly restore pipeline states to bypass expensive API calls and debug broken sub-pipelines using the `--replay` flag.
* **Model Agnostic:** Swap between Groq, OpenAI, or local Ollama models instantly via environment variables.
* **Native VS Code Extension:** Includes a `.vsix` installer for full syntax highlighting.

---

## Installation & Setup

**1. Clone the repository**
`git clone https://github.com/rolfe099-sketch/aion_compiler.git`
`cd aion_compiler`
`pip install -r requirements.txt`

**2. Install the VS Code Extension**
To get syntax highlighting for `.aion` files:
1. Open VS Code.
2. Go to the Extensions panel
3. Click the `...` at the top right -> **Install from VSIX...**
4. Select the `aion-1.0.0.vsix` file included in this repository. (Published by "end").

**3. Set your API Key**
Aion uses the universal OpenAI client, meaning it supports almost any provider. Set your environment variable in your terminal:

**For Groq (Default)**
`$env:AION_API_KEY="your_groq_key"`

**For OpenAI**
`$env:AION_BASE_URL="https://api.openai.com/v1"`
`$env:AION_API_KEY="your_openai_key"`

---

## Tutorial: Writing Your First Pipeline

Aion works by defining **Agents** (the LLMs), **Prompts** (their instructions), and the **Pipeline** (the execution path). Open `main.aion` and define your flow:

[Agent :: Coder]
@model -> "llama-3.1-8b-instant"
@temp  -> 0.2

[Prompt :: CoderRules]
>> "You are a Python developer. Write exactly one Python script."
>> "Your task is: " $task

// The Execution DAG (Directed Acyclic Graph)
UserStream |> CoderRules |> Coder |> SystemShell |> ?{
    "[EXECUTION SUCCESS]" -> FileOut
    "[EXECUTION FAILED]" -> CriticRules |> Critic |> TerminalOut
}

### Running the Compiler
Execute the pipeline from your terminal, passing in the `$task` variable:
`python compiler.py main.aion --task "Write a script that prints a dataframe, but spell pandas as 'pandaz'"`

The MatchGate (`?{ ... }`) will automatically detect the Python crash, branch the execution, and route the traceback to the Critic agent to fix it.

### Time-Travel Debugging
If a pipeline crashes deep in the execution tree, don't pay for the early API calls again. Instantly resume from the exact state using the replay flag:
`python compiler.py main.aion --replay CriticRules`

Aion will read the local state cache, inject the failed payload, and immediately ping the Critic.

---

## License
MIT License. Feel free to fork, modify, and build your own tools on top of the Aion engine.