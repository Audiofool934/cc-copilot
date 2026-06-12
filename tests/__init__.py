import os

# The cockpit auto-shows a first-run WelcomeScreen when no ~/.cc-copilot.toml
# exists (config absence is the "not onboarded yet" sentinel). CI has no config,
# so without this guard every TUI test would get that modal pushed on mount and
# its focus/assertions would break. Onboarding's own tests opt back in by
# popping this var and pointing CC_COPILOT_CONFIG at a temp path.
os.environ.setdefault("CC_COPILOT_NO_ONBOARD", "1")

# Isolate the whole suite from the DEVELOPER'S real ~/.cc-copilot.toml. Any
# test that builds a session/cockpit without overriding CC_COPILOT_CONFIG would
# otherwise read the real config and export its [env] table (API keys!) into
# this process — poisoning os.environ for every later test (e.g. the
# WelcomeScreen "no key → don't save" gate) in a machine-dependent, order-
# dependent way. CI never sees that because it has no config file; this makes
# a developer machine behave like CI. Tests that want a config set their own.
os.environ.setdefault("CC_COPILOT_CONFIG",
                      os.path.join(os.path.dirname(__file__), "no-such-config.toml"))

# Same hazard, one level down: a key already exported in the developer's SHELL
# (or leaked by an earlier in-process config load) would defeat key-gating
# assertions. Tests never need real provider keys — drop them.
for _k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
           "MOONSHOT_API_KEY", "ZAI_API_KEY", "DASHSCOPE_API_KEY",
           "DASHSCOPE_API_BASE", "GROQ_API_KEY", "XAI_API_KEY",
           "GEMINI_API_KEY", "MISTRAL_API_KEY", "TOGETHER_API_KEY",
           "FIREWORKS_API_KEY", "CEREBRAS_API_KEY", "DEEPINFRA_API_KEY",
           "HUGGINGFACE_API_KEY", "NVIDIA_API_KEY", "CHUTES_API_KEY",
           "NOVITA_API_KEY", "VENICE_API_KEY", "ARCEE_API_KEY",
           "GMI_API_KEY", "STEPFUN_API_KEY", "XIAOMI_API_KEY",
           "VOLCENGINE_API_KEY", "TENCENT_TOKENHUB_API_KEY",
           "CC_COPILOT_API_KEY", "CC_COPILOT_API_BASE", "CC_COPILOT_BACKEND",
           "CC_COPILOT_LLM_CMD", "CC_COPILOT_MODEL"):
    os.environ.pop(_k, None)
