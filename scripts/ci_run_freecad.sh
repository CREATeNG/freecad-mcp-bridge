#!/usr/bin/env bash
# Run test_install.py or test_verify.py inside FreeCAD and publish the CI log.
#
# Usage: ci_run_freecad.sh install|verify
#
# Expects GitHub Actions env: RUNNER_OS, RUNNER_TEMP, FC_CI_HOME, GITHUB_WORKSPACE
# Optional: FREECAD_BIN, ISOLATE_HOME=true, QT_QPA_PLATFORM

set -euo pipefail

PHASE="${1:-}"
if [[ "$PHASE" != "install" && "$PHASE" != "verify" ]]; then
  echo "Usage: ci_run_freecad.sh install|verify" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TIMEOUT_SEC=900

case "$PHASE" in
  install)
    SCRIPT="${REPO_ROOT}/scripts/test_install.py"
    CI_LOG="${RUNNER_TEMP}/freecad-ci-install.log"
    GROUP_TITLE="Install addon via Addon Manager"
    ;;
  verify)
    SCRIPT="${REPO_ROOT}/scripts/test_verify.py"
    CI_LOG="${RUNNER_TEMP}/freecad-ci-verify.log"
    GROUP_TITLE="Verify addon after restart"
    ;;
esac

resolve_freecad_bin() {
  if [[ -n "${FREECAD_BIN:-}" && -f "${FREECAD_BIN}" ]]; then
    return 0
  fi

  if [[ "${RUNNER_OS:-}" == "Windows" ]]; then
    local candidates=() candidate pf86
    if [[ -n "${ProgramFiles:-}" ]]; then
      candidates+=("${ProgramFiles}/FreeCAD 1.1/bin/FreeCAD.exe")
    fi
    if [[ -n "${PROGRAMFILES:-}" ]]; then
      candidates+=("${PROGRAMFILES}/FreeCAD 1.1/bin/FreeCAD.exe")
    fi
    pf86="$(cmd //c "echo %ProgramFiles(x86)%" 2>/dev/null | tr -d '\r')"
    if [[ -n "$pf86" && "$pf86" != *"%ProgramFiles(x86)%"* ]]; then
      candidates+=("${pf86}/FreeCAD 1.1/bin/FreeCAD.exe")
    fi
    if [[ -n "${LOCALAPPDATA:-}" ]]; then
      candidates+=("${LOCALAPPDATA}/Programs/FreeCAD 1.1/bin/FreeCAD.exe")
    fi
    for candidate in "${candidates[@]}"; do
      if [[ -f "$candidate" ]]; then
        FREECAD_BIN="$candidate"
        echo "Using FreeCAD: ${FREECAD_BIN}"
        return 0
      fi
    done
    echo "FreeCAD.exe not found after winget install" >&2
    return 1
  fi

  if command -v freecad >/dev/null 2>&1; then
    FREECAD_BIN="$(command -v freecad)"
  elif command -v FreeCAD >/dev/null 2>&1; then
    FREECAD_BIN="$(command -v FreeCAD)"
  elif [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/freecad" ]]; then
    FREECAD_BIN="${CONDA_PREFIX}/bin/freecad"
  elif [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/FreeCAD" ]]; then
    FREECAD_BIN="${CONDA_PREFIX}/bin/FreeCAD"
  else
    echo "FreeCAD binary not found in PATH or conda env" >&2
    conda list freecad 2>/dev/null || true
    ls -la "${CONDA_PREFIX:-}/bin" 2>/dev/null || true
    return 1
  fi
  echo "Using FreeCAD: ${FREECAD_BIN}"
}

setup_profile() {
  if [[ "${ISOLATE_HOME:-false}" == "true" ]]; then
    export HOME="${FC_CI_HOME}"
    export USERPROFILE="${FC_CI_HOME}"
  fi
  if [[ "${RUNNER_OS:-}" == "Windows" ]]; then
    mkdir -p "${FC_CI_HOME}/AppData/Roaming"
  fi
}

run_with_timeout() {
  if [[ "${RUNNER_OS:-}" == "Linux" ]]; then
    xvfb-run -a timeout "${TIMEOUT_SEC}" "$@"
    return
  fi
  if [[ "${RUNNER_OS:-}" == "macOS" ]]; then
    export LIBGL_ALWAYS_SOFTWARE=1
    perl -e 'alarm shift; exec @ARGV' "${TIMEOUT_SEC}" "$@"
    return
  fi
  if command -v timeout >/dev/null 2>&1; then
    timeout "${TIMEOUT_SEC}" "$@"
    return
  fi
  if command -v perl >/dev/null 2>&1; then
    perl -e 'alarm shift; exec @ARGV' "${TIMEOUT_SEC}" "$@"
    return
  fi
  "$@"
}

resolve_freecad_bin
setup_profile

if [[ ! -f "$SCRIPT" ]]; then
  echo "Test script not found: ${SCRIPT}" >&2
  exit 1
fi

export RELEASE_INSTALL_CI_LOG="${CI_LOG}"
rm -f "${RELEASE_INSTALL_CI_LOG}"

echo "Launching '${FREECAD_BIN}' with '${SCRIPT}'"
freecad_exit=0
run_with_timeout "${FREECAD_BIN}" "${SCRIPT}" || freecad_exit=$?

publish_exit=0
bash "${SCRIPT_DIR}/publish_ci_log.sh" "${CI_LOG}" "${GROUP_TITLE}" || publish_exit=$?

if [[ "$freecad_exit" -ne 0 ]]; then
  echo "FreeCAD exited with code ${freecad_exit} running ${SCRIPT}" >&2
  exit "$freecad_exit"
fi
exit "$publish_exit"