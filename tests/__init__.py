import os

# The cockpit auto-shows a first-run WelcomeScreen when no ~/.cc-copilot.toml
# exists (config absence is the "not onboarded yet" sentinel). CI has no config,
# so without this guard every TUI test would get that modal pushed on mount and
# its focus/assertions would break. Onboarding's own tests opt back in by
# popping this var and pointing CC_COPILOT_CONFIG at a temp path.
os.environ.setdefault("CC_COPILOT_NO_ONBOARD", "1")
