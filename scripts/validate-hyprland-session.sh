#!/usr/bin/env bash
#
# Functional-session smoke test for the Hyprland stack.
#
# Confirms that a Hyprland session is running offline: the compositor answers
# queries, the session is live, and the essential companion daemons (wallpaper,
# screen locker, idle daemon, desktop portal) are running. Exits 0 (pass) or
# 1 (fail). Repeatable on a fresh target.
#
# By default an already-running session is validated. With --start a session is
# launched first (on the current or a chosen VT) and torn down afterwards, so a
# fresh target can be validated end to end. With --lock the session is locked
# before the companion checks so the screen locker is exercised while it is
# actually resident.
#
# Usage:
#   validate-hyprland-session.sh [--start] [--vt N] [--lock]
#                                [--timeout SECONDS] [--keep]
#
# The script must run as the target user on their own seat, not as root, and
# it performs no network access.
#
# Environment:
#   XDG_RUNTIME_DIR, WAYLAND_DISPLAY   used when talking to the compositor
#   DISPLAY                           unused (Wayland session)

set -euo pipefail

COMPANIONS=(hyprpaper hyprlock hypridle xdg-desktop-portal-hyprland)
TIMEOUT=15
VT=""
START=0
KEEP=0
LOCK=0
STARTED=0
LOG_FILE="${XDG_RUNTIME_DIR:-/tmp}/hyprland-smoke.log"

usage() {
    sed -n '2,30p' "$0"
    exit 2
}

die() {
    echo "FAIL: $1" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --start) START=1 ;;
        --vt) VT="$2"; shift ;;
        --lock) LOCK=1 ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --keep) KEEP=1 ;;
        -h | --help) usage ;;
        *) echo "unknown option: $1" >&2; usage ;;
    esac
    shift
done

if [ "$(id -u)" -eq 0 ]; then
    die "run as the target user on their own seat, not as root"
fi

if command -v hyprctl >/dev/null 2>&1; then
    HAVE_HYPRCTL=1
else
    HAVE_HYPRCTL=0
fi

compositor_running() {
    [ "$HAVE_HYPRCTL" -eq 1 ] && hyprctl version >/dev/null 2>&1
}

start_session() {
    if compositor_running; then
        echo "note: a compositor is already running; --start skipped the launch"
        return 0
    fi
    if ! command -v Hyprland >/dev/null 2>&1; then
        die "Hyprland is not installed on this target"
    fi
    local tty=""
    if [ -n "$VT" ]; then
        tty="/dev/tty$VT"
        if [ ! -w "$tty" ]; then
            die "cannot access $tty as the current user"
        fi
    fi
    local launched=0
    if [ -n "$tty" ]; then
        if command -v openvt >/dev/null 2>&1; then
            openvt -s -c "$VT" -- Hyprland >"$LOG_FILE" 2>&1 &
            launched=1
        else
            die "--vt needs openvt on the target"
        fi
    else
        Hyprland >"$LOG_FILE" 2>&1 &
        launched=1
    fi
    if [ "$launched" -eq 1 ]; then
        STARTED=1
        echo "started Hyprland (log: $LOG_FILE)"
    fi
}

wait_for_compositor() {
    local waited=0
    while [ "$waited" -lt "$TIMEOUT" ]; do
        if compositor_running; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 1
}

stop_session() {
    if [ "$STARTED" -eq 1 ] && compositor_running; then
        hyprctl dispatch exit >/dev/null 2>&1 || true
        STARTED=0
        echo "stopped the smoke-test session"
    fi
}

trap 'stop_session' EXIT

check() {
    local name="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "PASS: $name"
    else
        echo "FAIL: $name"
        FAILURES=$((FAILURES + 1))
    fi
}

FAILURES=0

if [ "$START" -eq 1 ]; then
    start_session
fi

if ! wait_for_compositor; then
    die "the compositor did not answer within ${TIMEOUT}s (log: $LOG_FILE)"
fi

check "compositor answers (hyprctl version)" hyprctl version
check "session is live (hyprctl activeworkspace)" hyprctl activeworkspace

if [ "$LOCK" -eq 1 ]; then
    hyprctl dispatch lock >/dev/null 2>&1 || true
    sleep 2
fi

for name in "${COMPANIONS[@]}"; do
    check "companion running: $name" pgrep -x "$name"
done

check "portal answers (dbus)" \
    dbus-send --session --print-reply \
    --dest=org.freedesktop.impl.portal.Desktop \
    /org/freedesktop/portal/desktop \
    org.freedesktop.DBus.Properties.Get \
    string:org.freedesktop.impl.portal.Background string:version

if [ "$FAILURES" -eq 0 ]; then
    echo "result: PASS"
    exit 0
fi
echo "result: FAIL ($FAILURES check(s) failed)"
exit 1
