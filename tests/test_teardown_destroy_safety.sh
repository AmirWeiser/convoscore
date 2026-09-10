#!/usr/bin/env bash
# Focused, dependency-free verification for scripts/teardown.sh's terraform
# destroy / LocalStack removal ordering: LocalStack must be removed only
# after a successful `terraform destroy`, or when there is definitively no
# Terraform-managed state to destroy - never after a failed destroy, and
# never when there's a nonempty state we simply can't reach. Runs entirely
# against stubbed `terraform`/`docker` binaries on PATH - no real cluster,
# LocalStack, or Terraform state involved. Run with: bash tests/test_teardown_destroy_safety.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

STUB_BIN="$WORK/bin"
mkdir -p "$STUB_BIN"

cat > "$STUB_BIN/terraform" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "state" ] && [ "$2" = "list" ]; then
  if [ "${FAKE_TF_HAS_RESOURCES:-0}" = "1" ]; then
    echo "aws_s3_bucket.conversations"
  fi
  exit 0
fi
if [ "$1" = "destroy" ]; then
  exit "${FAKE_TF_DESTROY_EXIT:-0}"
fi
exit 0
EOF
chmod +x "$STUB_BIN/terraform"

cat > "$STUB_BIN/docker" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "$DOCKER_CALL_LOG"
if [ "$1" = "ps" ]; then
  if [ "${FAKE_LOCALSTACK_RUNNING:-0}" = "1" ]; then
    echo "convoscore-localstack"
  fi
  exit 0
fi
exit 0
EOF
chmod +x "$STUB_BIN/docker"

export PATH="$STUB_BIN:$PATH"

FAILED=0

_run_scenario() {
  local name="$1" has_state="$2" tf_exit="$3" ls_running="$4" expect_exit="$5" expect_rm="$6"

  local tf_dir="$WORK/tf-$name"
  mkdir -p "$tf_dir"
  if [ "$has_state" = "1" ]; then
    mkdir -p "$tf_dir/.terraform"
    echo '{}' > "$tf_dir/terraform.tfstate"
  fi

  DOCKER_CALL_LOG="$WORK/docker-calls-$name.log"
  : > "$DOCKER_CALL_LOG"

  (
    export FAKE_TF_HAS_RESOURCES="$has_state"
    export FAKE_TF_DESTROY_EXIT="$tf_exit"
    export FAKE_LOCALSTACK_RUNNING="$ls_running"
    export DOCKER_CALL_LOG
    set -euo pipefail
    source "$REPO_ROOT/scripts/teardown.sh"
    destroy_terraform_and_localstack "$tf_dir"
  )
  local actual_exit=$?

  local did_rm=0
  grep -q "^rm -f convoscore-localstack$" "$DOCKER_CALL_LOG" && did_rm=1

  if [ "$actual_exit" -eq 0 ] && [ "$expect_exit" != "0" ]; then
    echo "FAIL [$name]: expected non-zero exit, got 0" >&2
    FAILED=1
  elif [ "$actual_exit" -ne 0 ] && [ "$expect_exit" = "0" ]; then
    echo "FAIL [$name]: expected exit 0, got $actual_exit" >&2
    FAILED=1
  fi

  if [ "$did_rm" != "$expect_rm" ]; then
    echo "FAIL [$name]: expected docker rm called=$expect_rm, got $did_rm" >&2
    FAILED=1
  fi

  if [ "$FAILED" = "0" ]; then
    echo "PASS [$name]"
  fi
}

# name, has_state, tf_destroy_exit, localstack_running, expect_exit(0/nonzero), expect_rm_called(0/1)
_run_scenario "destroy_fails_localstack_never_removed" 1 1 1 nonzero 0
_run_scenario "destroy_succeeds_localstack_removed"     1 0 1 0       1
_run_scenario "no_state_localstack_removed"             0 0 0 0       1
_run_scenario "resources_but_localstack_down_refused"   1 0 0 nonzero 0

if [ "$FAILED" != "0" ]; then
  echo "teardown destroy-safety verification FAILED" >&2
  exit 1
fi
echo "teardown destroy-safety verification: all scenarios passed"
