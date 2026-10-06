"""
Choose the models for the workflow. Run this before main.py whenever you want
to change models or check your subscriptions:

    python3 setup_models.py          # check providers, pick models, test them, save
    python3 setup_models.py --check  # only show which providers are active

Providers: Gemini Canvas proxy, Claude (subscription or API key), DeepSeek API,
OpenCode Go. API keys are saved in llm_keys.env (kept out of git); model
choices are saved in llm_variables.json.
"""

import importlib.util
import os
import subprocess
import sys

import llm_providers as lp

ICON = {"active": "✅", "off": "⚪", "error": "❌"}
ROLE_HELP = {
    "main_model": "MAIN model: writes the script, and runs any step whose own model fails",
    "single_model": "ONE STRONG model: used for every step when you pick option 2 in main.py",
    "backup_model": "BACKUP model (optional): runs a step when the main model fails, e.g. Gemini Canvas timed out\n"
                    "   or the tab closed. Type 0 for no backup",
}


def ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        answer = ""
    return answer or default


# import name -> pip package
REQUIRED = {"openai": "openai", "anthropic": "anthropic", "dotenv": "python-dotenv",
            "langgraph": "langgraph", "langchain_openai": "langchain-openai", "langchain_core": "langchain-core"}


def missing_packages() -> list:
    return [pkg for mod, pkg in REQUIRED.items() if importlib.util.find_spec(mod) is None]


def check_packages():
    """Tells you which Python packages are missing and offers to install them."""
    missing = missing_packages()
    if not missing:
        return
    cmd = [sys.executable, "-m", "pip", "install", *missing]
    print("\nMissing Python packages: " + ", ".join(missing))
    print("  Install with:  " + " ".join(["python3", "-m", "pip", "install", *missing]))
    print("  (Homebrew Python may need: python3 -m pip install --user --break-system-packages " + " ".join(missing) + ")")
    if ask("Install now? [Y/n] ", "y").lower() != "y":
        return
    if subprocess.run(cmd).returncode != 0:
        print("  Retrying with --user --break-system-packages (Homebrew Python)...")
        subprocess.run(cmd[:4] + ["--user", "--break-system-packages"] + missing)
    importlib.invalidate_caches()
    still = missing_packages()
    print("  ✅ Installed." if not still else f"  ❌ Still missing: {', '.join(still)}")


def save_key(env_name: str, value: str):
    """Adds or replaces NAME=value in llm_keys.env and keeps the file out of git."""
    lines = []
    if os.path.exists(lp.KEYS_PATH):
        with open(lp.KEYS_PATH, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if not l.startswith(env_name + "=")]
    value = value.strip()
    lines.append(f"{env_name}={value}")
    with open(lp.KEYS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.environ[env_name] = value
    gi = ".gitignore"
    existing = open(gi, encoding="utf-8").read().splitlines() if os.path.exists(gi) else []
    if lp.KEYS_PATH not in existing:
        with open(gi, "a", encoding="utf-8") as f:
            f.write(lp.KEYS_PATH + "\n")


def show_status(config: dict) -> dict:
    print("\nProviders")
    print("---------")
    status = {}
    for name, cfg in config["providers"].items():
        state, detail = lp.provider_status(name, cfg)
        status[name] = state
        extra = f" via {cfg.get('transport')}" if cfg.get("type") == "claude" else ""
        print(f"  {ICON[state]} {name:<9} {cfg.get('label', name)}{extra}: {detail}")
    return status


def offer_proxy_token(config: dict, status: dict) -> bool:
    """The Gemini Canvas proxy keeps its token in <proxy folder>/native_host/.proxy_token."""
    gem = config["providers"].get("gemini", {})
    if gem.get("type") != "canvas_proxy" or status.get("gemini") == "active" or gem.get("api_key"):
        return False
    folder = ask("  Gemini Canvas proxy isn't answering. Path to your gemini-canvas-proxy folder\n"
                 "  (to read its token; Enter to skip): ")
    folder = os.path.expanduser(folder.strip().strip('"').strip("'"))
    token_file = os.path.join(folder, "native_host", ".proxy_token")
    if folder and os.path.exists(token_file):
        gem["token_file"] = token_file
        print(f"  Using the token in {token_file}")
        return True
    if folder:
        print(f"  No token found at {token_file} (run the proxy's ./setup.sh first).")
    return False


def offer_keys(config: dict, status: dict) -> bool:
    changed = offer_proxy_token(config, status)
    for name, cfg in config["providers"].items():
        env = cfg.get("api_key_env")
        if status.get(name) == "off" and env and not cfg.get("free"):
            key = ask(f"  Paste your {cfg.get('label', name)} API key to enable it (Enter to skip): ")
            if key:
                save_key(env, key)
                changed = True
    claude = config["providers"].get("claude", {})
    if status.get("claude") != "active" and claude.get("transport") != "anthropic_api" and os.environ.get("ANTHROPIC_API_KEY"):
        if ask("  Claude subscription isn't working but ANTHROPIC_API_KEY is set. Use the API key for Claude? [y/N] ").lower() == "y":
            claude["transport"] = "anthropic_api"
            changed = True
    return changed


def collect_models(config: dict, status: dict) -> list:
    """Numbered list of 'provider:model' from the active providers, plus the
    models already in the config (so a provider whose model list can't be read
    still shows what you use)."""
    specs = []
    for name, cfg in config["providers"].items():
        if status.get(name) != "active":
            continue
        try:
            specs += [f"{name}:{m}" for m in lp.list_models(name, cfg)]
        except Exception:
            pass
    current = [config["main_model"], config["single_model"], *config["hybrid_routes"].values()]
    if config.get("backup_model"):
        current.append(config["backup_model"])
    for spec in current:
        if spec not in specs:
            specs.append(spec)
    return specs


def label(spec: str, config: dict) -> str:
    """'opencode:qwen3.8-max (messages)': OpenCode Go models show which API they use."""
    provider, model = lp.parse_spec(spec)
    cfg = config["providers"].get(provider, {})
    if cfg.get("type") == "opencode_go":
        endpoint = lp.opencode_endpoint(model, cfg)
        if endpoint == "messages" and importlib.util.find_spec("anthropic") is None:
            return f"{spec} (messages: needs the anthropic package)"
        if endpoint != "chat":
            return f"{spec} ({endpoint})"
    return spec


def warn_same_model(config: dict):
    """Hybrid only helps if the judges aren't the writer grading itself."""
    main = config["main_model"]
    judges = set(config["hybrid_routes"].values())
    if judges == {main}:
        print(f"\n⚠️  Every Hybrid judge is the main model ({main}): the writer would grade its own work,")
        print("   which is why scores were always 10/10 before. Pick judges from another family,")
        print("   e.g. writer opencode:qwen3.8-max with judges on deepseek:deepseek-v4-pro or opencode:kimi-k3.")
    elif main in judges:
        same = [n for n, m in config["hybrid_routes"].items() if m == main]
        print(f"\nNote: {', '.join(same)} use the main model, so those steps grade their own writing.")
    if config.get("backup_model") and config["backup_model"] == main:
        print(f"Note: the backup model is the same as the main model, so it adds nothing. Pick another or 0 for none.")


def print_models(specs: list, config: dict):
    print("\nAvailable models")
    print("----------------")
    specs = [label(s, config) for s in specs]
    print("Tip: the MAIN model writes ~20 times per run, the judges ~5 times each. A slow model (see the")
    print("     seconds in the test below) is better as a judge than as the main model.\n")
    width = max(len(s) for s in specs) + 6
    cols = max(1, min(3, 110 // width))
    rows = (len(specs) + cols - 1) // cols
    for r in range(rows):
        line = ""
        for c in range(cols):
            i = c * rows + r
            if i < len(specs):
                line += f"{i + 1:>3}) {specs[i]:<{width - 5}}"
        print(line.rstrip())


def pick(label: str, current: str, specs: list, allow_none: bool = False) -> str:
    while True:
        answer = ask(f"{label}\n   current: {current or 'none'}\n   number, provider:model, or Enter to keep: ", current)
        if allow_none and answer in ("0", ""):
            return "" if answer == "0" else current
        if answer.isdigit() and 1 <= int(answer) <= len(specs):
            return specs[int(answer) - 1]
        if ":" in answer:
            return answer
        print("   Please type a number from the list, or provider:model.")


def choose_and_test(config: dict, specs: list):
    print("\nChoose a model for each role (Enter keeps the current one).\n")
    for role in ("main_model", "single_model"):
        config[role] = pick(ROLE_HELP[role], config[role], specs)
    config["backup_model"] = pick(ROLE_HELP["backup_model"], config.get("backup_model", ""), specs, allow_none=True)
    if config["backup_model"] == config["main_model"]:
        print("   (Same as the main model, so it can't help when the main model fails. Consider another provider.)")
    print("\nHYBRID judges (option 3 in main.py): each of these steps runs on its own model:")
    for node, spec in list(config["hybrid_routes"].items()):
        config["hybrid_routes"][node] = pick(f" • {node}", spec, specs)

    warn_same_model(config)
    while True:
        chosen = sorted({config["main_model"], config["single_model"], *config["hybrid_routes"].values(),
                         *([config["backup_model"]] if config.get("backup_model") else [])})
        print("\nTesting each chosen model with a one-word call...")
        failed = []
        for spec in chosen:
            ok, detail = lp.test_model(spec, config["providers"])
            print(f"  {'✅' if ok else '❌'} {spec}: {detail}")
            if not ok:
                failed.append(spec)
        if not failed:
            return
        print("\nModels marked ❌ will be skipped at run time (their steps run on the main model).")
        if config["main_model"] in failed:
            print("⚠️  Your MAIN model failed. Runs need it: pick another, or start its service (e.g. the Gemini Canvas tab).")
        if ask("Re-pick the failed models? [Y/n] ", "y").lower() != "y":
            return
        for role in ("main_model", "single_model", "backup_model"):
            if config.get(role) and config[role] in failed:
                config[role] = pick(ROLE_HELP[role], config[role], specs, allow_none=role == "backup_model")
        for node, spec in list(config["hybrid_routes"].items()):
            if spec in failed:
                config["hybrid_routes"][node] = pick(f" • {node}", spec, specs)


def main():
    check_packages()
    config = lp.load_llm_config()
    status = show_status(config)
    if "--check" in sys.argv:
        return
    if offer_keys(config, status):
        status = show_status(config)

    specs = collect_models(config, status)
    print_models(specs, config)
    choose_and_test(config, specs)

    mode = ask("\nDefault option when main.py can't show its menu: 1) main  2) single  3) hybrid  [3]: ", "3")
    config["provider"] = {"1": "main", "2": "single"}.get(mode, "hybrid")
    lp.save_llm_config(config)
    print(f"\nSaved to {lp.CONFIG_PATH}. Now run: python3 main.py <script.txt>")


if __name__ == "__main__":
    main()
