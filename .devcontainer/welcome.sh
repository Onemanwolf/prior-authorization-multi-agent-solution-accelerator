#!/bin/bash
if [ -f /tmp/.devcontainer-setup-complete ]; then
  echo ""
  echo "✅  Environment ready!"
  echo "    Run: azd auth login && az login && azd up"
  echo ""
fi
