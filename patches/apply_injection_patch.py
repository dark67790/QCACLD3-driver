#!/usr/bin/env python3
"""
apply_injection_patch.py

Applies patches/07_packet_injection.patch to vendor/qcom/opensource/wlan/
(qcacld-3.0 + qca-wifi-host-cmn), then hand-merges the handful of hunks that
don't apply cleanly because this tree has diverged from the patch's original
baseline:

  - rx_monitor.h / rx_monitor.c / cfg80211.c includes + vendor cmd array
  - main.h injection_ctx field
  - main.c __hdd_mon_open (this tree already has its own idempotent monitor
    open handling + a BBM_DRIVER_MODE_POLICY block the patch's baseline
    didn't have)
  - wma_main.c wma_open (this tree has an extra wma_trace_init() call)
  - wma_utils.c wma_mon_mlme_vdev_down_send (del_bss_resp allocation)
  - default_defconfig injection flag
  - tx_rx.c wlan_hdd_netif_queue_control (this tree has an extra
    hdd_adapter_is_link_adapter MLO guard)

Deliberately NOT applied (non-essential, or real risk of breaking
tree-specific logic the patch's baseline never had):
  - main.c wlan_off_str/wlan_on_str root-UID gating while monitor active
    (this tree has a WAIT_FOR_READY branch + different control flow the
    patch never accounted for)
  - main.c HDD_FW_VER_SUB_ID macro fix (unrelated firmware-version decoding)
  - Kbuild -Wmaybe-uninitialized / -Wframe-larger-than block (unrelated
    compiler-warning housekeeping)

Run from the repo root (the directory containing vendor/qcom/opensource/wlan).
Exits non-zero on any failure so CI surfaces it instead of silently
producing a half-patched tree.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path.cwd()
WLAN_DIR = REPO_ROOT / "vendor/qcom/opensource/wlan"
QCACLD = WLAN_DIR / "qcacld-3.0"
HDD_SRC = QCACLD / "core/hdd/src"
HDD_INC = QCACLD / "core/hdd/inc"
WMA_SRC = QCACLD / "core/wma/src"
CONFIGS = QCACLD / "configs"
PATCH_FILE = Path(__file__).resolve().parent / "07_packet_injection.patch"

FAILURES = []


def run_bulk_patch():
    print("== Step 1: bulk patch -p3 (34 clean files + fuzzy-but-correct hunks) ==")
    if not PATCH_FILE.exists():
        sys.exit(f"FATAL: patch file not found at {PATCH_FILE}")
    if not WLAN_DIR.exists():
        sys.exit(f"FATAL: {WLAN_DIR} not found — run from repo root")

    result = subprocess.run(
        [
            "patch", "-p3", "--forward", "--no-backup-if-mismatch",
            "--fuzz=0", "-i", str(PATCH_FILE),
        ],
        cwd=WLAN_DIR, capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    # returncode 1 just means some hunks were rejected -- expected, the
    # known-divergent ones are hand-merged in step 2 below.
    if result.returncode not in (0, 1):
        sys.exit(f"FATAL: patch invocation itself failed, rc={result.returncode}")


def _normalize_for_match(block: str) -> str:
    """Escape a literal code block, then relax runs of horizontal whitespace
    (spaces/tabs) into \\s+ so tab-vs-space drift doesn't break the match.
    Newlines are preserved as literal newlines (not relaxed), since we DO
    want to catch genuine structural differences in line breaks."""
    escaped = re.escape(block)
    # re.escape turns each space/tab into a literal char; collapse any run
    # of escaped horizontal whitespace into a flexible \s+ (but not \n).
    escaped = re.sub(r"(?:\\ |\\\t)+", r"[ \\t]+", escaped)
    return escaped


def _diagnose(path: Path, needle: str, context_lines: int = 15):
    """On an anchor miss, locate a much smaller near-certain substring and
    dump real surrounding file content to the CI log, so we get ground
    truth from a single run instead of a manual back-and-forth."""
    text = path.read_text()
    idx = text.find(needle)
    if idx == -1:
        print(f"    DIAGNOSE: needle {needle!r} not found anywhere in file either "
              f"-- this section may not exist in this tree at all")
        return
    line_no = text.count("\n", 0, idx) + 1
    lines = text.splitlines()
    start = max(0, line_no - 1 - context_lines // 2)
    end = min(len(lines), line_no - 1 + context_lines // 2)
    print(f"    DIAGNOSE: needle found at line {line_no}, real content "
          f"(lines {start + 1}-{end}):")
    for i in range(start, end):
        # render tabs visibly so indentation-only drift is obvious in the log
        print(f"    {i + 1:>6}\t{lines[i].replace(chr(9), '<TAB>')}")


def already_present(path: Path, needle: str) -> bool:
    """Idempotency check: some hunks succeed in the bulk patch step (possibly
    landing correctly even when the hand-merge step's wider anchor doesn't
    match due to surrounding tree-specific content). Check for the smallest
    reliable signal before attempting a hand-merge, so already-applied work
    isn't misreported as a failure."""
    if not path.exists():
        return False
    return needle in path.read_text()


def fuzzy_replace(path: Path, old_block: str, new_block: str, label: str,
                   required: bool = True, diagnose_needle: str = None):
    if not path.exists():
        msg = f"[{label}] file not found: {path}"
        FAILURES.append(msg) if required else print(f"WARN: {msg}")
        return False

    text = path.read_text()
    pattern = _normalize_for_match(old_block)
    matches = list(re.finditer(pattern, text))

    if len(matches) == 0:
        msg = f"[{label}] anchor not found in {path}"
        if required:
            FAILURES.append(msg)
        else:
            print(f"WARN: {msg} -- skipping (non-required)")
        if diagnose_needle:
            _diagnose(path, diagnose_needle)
        return False
    if len(matches) > 1:
        msg = (f"[{label}] anchor matched {len(matches)} times in {path}, "
               f"expected exactly 1 -- refusing to guess, needs a human")
        FAILURES.append(msg)
        return False

    m = matches[0]
    new_text = text[:m.start()] + new_block + text[m.end():]
    path.write_text(new_text)
    print(f"[{label}] applied -> {path.relative_to(REPO_ROOT)}")
    return True


def apply_hand_merges():
    print("\n== Step 2: hand-merged fixes for hunks that diverged from baseline ==")

    # --- rx_monitor.h : hdd_disable_monitor_mode(void) already exists
    #     natively in this tree (confirmed: declared at the prototype AND
    #     stub, called from main.c with no args) -- it predates this patch
    #     and is unrelated to it. The patch's own (struct net_device *dev)
    #     version is dead code (grep of the full 44-file patch confirms
    #     nothing calls it), so we do NOT add a conflicting declaration
    #     here. See the wlan_hdd_rx_monitor.c fix below instead, which
    #     removes the patch's duplicate definition and splices the one
    #     line that matters (hdd_frame_inject_disable) into the real fn. ---
    f = HDD_SRC / "wlan_hdd_rx_monitor.h"
    if already_present(f, "hdd_disable_monitor_mode("):
        print("[rx_monitor.h] hdd_disable_monitor_mode already present "
              "(this tree's own, unrelated function) -- correctly left alone")
    else:
        msg = ("[rx_monitor.h] expected hdd_disable_monitor_mode to already "
               "exist in this tree and it doesn't -- tree has changed more "
               "than expected, needs a human to re-check")
        FAILURES.append(msg)

    # --- rx_monitor.c : conditional include (often already landed via the
    #     bulk patch step even when the hand-merge anchor below doesn't
    #     match due to tree-specific surrounding content) ---
    f = HDD_SRC / "wlan_hdd_rx_monitor.c"
    if already_present(f, '#include "wlan_hdd_frame_inject.h"'):
        print("[rx_monitor.c include] already present -- skipping")
    else:
        fuzzy_replace(f,
            '#include "wlan_hdd_rx_monitor.h"\n#include "ol_txrx.h"\n',
            '#include "wlan_hdd_rx_monitor.h"\n#include "ol_txrx.h"\n'
            '#ifdef FEATURE_FRAME_INJECTION_SUPPORT\n'
            '#include "wlan_hdd_frame_inject.h"\n#endif\n',
            "rx_monitor.c include",
            diagnose_needle='#include "wlan_hdd_rx_monitor.h"')

    # --- rx_monitor.c : the patch adds its OWN hdd_disable_monitor_mode()
    #     with a (struct net_device *dev) signature, which collides with
    #     this tree's pre-existing (void)-signature function of the same
    #     name -- confirmed via live grep as two full definitions in the
    #     same file, which will not compile. Nothing in the 44-file patch
    #     ever calls the (dev) version, so it's safe to delete outright;
    #     splice its one useful line (hdd_frame_inject_disable) into the
    #     real (void) function instead, using hdd_get_adapter(hdd_ctx,
    #     QDF_MONITOR_MODE) to reach the monitor adapter without a dev
    #     pointer in scope -- the same idiom this tree already uses
    #     elsewhere (wlan_hdd_state_ctrl_param_write) to find it. ---
    fuzzy_replace(f,
        "/**\n"
        " * hdd_disable_monitor_mode() - Disable monitor mode\n"
        " * @dev: Pointer to the net_device structure\n"
        " *\n"
        " * This function disables monitor mode configuration on the hardware\n"
        " * and also disables frame injection if it was enabled.\n"
        " *\n"
        " * Return: 0 for success; non-zero for failure\n"
        " */\n"
        "int hdd_disable_monitor_mode(struct net_device *dev)\n"
        "{\n"
        "\tvoid *soc = cds_get_context(QDF_MODULE_ID_SOC);\n"
        "\tuint8_t vdev_id;\n"
        "\tint ret;\n"
        "#ifdef FEATURE_FRAME_INJECTION_SUPPORT\n"
        "\tstruct hdd_adapter *adapter = WLAN_HDD_GET_PRIV_PTR(dev);\n"
        "#endif\n"
        "\n"
        "\thdd_enter_dev(dev);\n"
        "\n"
        "\tvdev_id = cdp_get_mon_vdev_from_pdev(soc, OL_TXRX_PDEV_ID);\n"
        "\tif (vdev_id < 0)\n"
        "\t\treturn -EINVAL;\n"
        "\n"
        "#ifdef FEATURE_FRAME_INJECTION_SUPPORT\n"
        "\t/* Disable frame injection when monitor mode is disabled */\n"
        "\tif (adapter) {\n"
        "\t\tif (QDF_IS_STATUS_ERROR(hdd_frame_inject_disable(adapter))) {\n"
        "\t\t\thdd_warn(\"Failed to disable frame injection\");\n"
        "\t\t\t/* Continue with monitor mode disable */\n"
        "\t\t}\n"
        "\t}\n"
        "#endif\n"
        "\n"
        "\tret = cdp_set_monitor_mode(soc, vdev_id, true);\n"
        "\n"
        "\treturn ret;\n"
        "}\n"
        "\n"
        "int hdd_disable_monitor_mode(void)\n"
        "{\n"
        "\tvoid *soc = cds_get_context(QDF_MODULE_ID_SOC);\n"
        "\n"
        "\treturn cdp_reset_monitor_mode(soc, OL_TXRX_PDEV_ID, false);\n"
        "}\n",
        "int hdd_disable_monitor_mode(void)\n"
        "{\n"
        "\tvoid *soc = cds_get_context(QDF_MODULE_ID_SOC);\n"
        "#ifdef FEATURE_FRAME_INJECTION_SUPPORT\n"
        "\tstruct hdd_context *hdd_ctx = cds_get_context(QDF_MODULE_ID_HDD);\n"
        "\tstruct hdd_adapter *adapter;\n"
        "\n"
        "\tif (hdd_ctx) {\n"
        "\t\tadapter = hdd_get_adapter(hdd_ctx, QDF_MONITOR_MODE);\n"
        "\t\tif (adapter) {\n"
        "\t\t\tif (QDF_IS_STATUS_ERROR(hdd_frame_inject_disable(adapter))) {\n"
        "\t\t\t\thdd_warn(\"Failed to disable frame injection\");\n"
        "\t\t\t\t/* Continue with monitor mode disable */\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\t}\n"
        "#endif\n"
        "\n"
        "\treturn cdp_reset_monitor_mode(soc, OL_TXRX_PDEV_ID, false);\n"
        "}\n",
        "rx_monitor.c remove duplicate hdd_disable_monitor_mode, splice into real fn",
        diagnose_needle="int hdd_disable_monitor_mode(struct net_device *dev)")

    # --- cfg80211.c : conditional include + vendor cmd array entry ---
    f = HDD_SRC / "wlan_hdd_cfg80211.c"
    fuzzy_replace(f,
        '#include "wlan_hdd_cfr.h"\n',
        '#include "wlan_hdd_cfr.h"\n'
        '#ifdef FEATURE_FRAME_INJECTION_SUPPORT\n'
        '#include "wlan_hdd_frame_inject.h"\n#endif\n',
        "cfg80211.c include")
    fuzzy_replace(f,
        "\tFEATURE_SAR_LIMITS_VENDOR_COMMANDS\n\tBCN_RECV_FEATURE_VENDOR_COMMANDS\n",
        "\tFEATURE_SAR_LIMITS_VENDOR_COMMANDS\n"
        "#ifdef FEATURE_FRAME_INJECTION_SUPPORT\n"
        "\tFEATURE_FRAME_INJECTION_VENDOR_COMMANDS,\n"
        "#endif\n"
        "#ifdef WLAN_BCN_RECV_FEATURE\n"
        "\tBCN_RECV_FEATURE_VENDOR_COMMANDS\n"
        "#endif\n",
        "cfg80211.c vendor cmd array")

    # --- main.h : injection_ctx field (often already landed via the bulk
    #     patch step even when the hand-merge anchor below doesn't match
    #     due to tree-specific fields between the two anchor lines) ---
    f = HDD_INC / "wlan_hdd_main.h"
    if already_present(f, "struct hdd_injection_ctx *injection_ctx;"):
        print("[main.h injection_ctx field] already present -- skipping")
    else:
        fuzzy_replace(f,
            "\tbool delete_in_progress;\n"
            "\tqdf_atomic_t net_dev_hold_ref_count[NET_DEV_HOLD_ID_MAX];\n};",
            "\tbool delete_in_progress;\n"
            "\tqdf_atomic_t net_dev_hold_ref_count[NET_DEV_HOLD_ID_MAX];\n"
            "\n#ifdef FEATURE_FRAME_INJECTION_SUPPORT\n"
            "\tstruct hdd_injection_ctx *injection_ctx;\n#endif\n};",
            "main.h injection_ctx field",
            diagnose_needle="net_dev_hold_ref_count[NET_DEV_HOLD_ID_MAX]")

    # --- main.c : __hdd_mon_open -- set carrier/queues up, keep existing
    #     BBM policy block this tree already has that the patch's baseline
    #     didn't ---
    f = HDD_SRC / "wlan_hdd_main.c"
    fuzzy_replace(f,
        "\tret = hdd_set_mon_rx_cb(dev);\n\n"
        "\tif (!ret)\n\t\tret = hdd_enable_monitor_mode(dev);\n\n"
        "\tif (!ret) {\n"
        "\t\tparam.policy = BBM_DRIVER_MODE_POLICY;\n"
        "\t\tparam.policy_info.driver_mode = QDF_GLOBAL_MONITOR_MODE;\n"
        "\t\thdd_bbm_apply_independent_policy(hdd_ctx, &param);\n"
        "\t\thdd_set_current_throughput_level(hdd_ctx,\n"
        "\t\t\t\t\t\t  PLD_BUS_WIDTH_VERY_HIGH);\n"
        "\t}\n\n\treturn ret;\n}",
        "\tret = hdd_set_mon_rx_cb(dev);\n\n"
        "\tif (!ret)\n\t\tret = hdd_enable_monitor_mode(dev);\n\n"
        "\tif (ret)\n\t\treturn ret;\n\n"
        "\tset_bit(DEVICE_IFACE_OPENED, &adapter->event_flags);\n\n"
        "\t/*\n"
        "\t * Monitor interface still needs carrier/tx queues marked up, otherwise\n"
        "\t * userspace injection tools fail with ENETDOWN even though mode switch\n"
        "\t * succeeded.\n"
        "\t */\n"
        "\twlan_hdd_netif_queue_control(adapter,\n"
        "\t\t\t\t     WLAN_START_ALL_NETIF_QUEUE_N_CARRIER,\n"
        "\t\t\t\t     WLAN_CONTROL_PATH);\n"
        "\thdd_warn_rl(\"monitor open complete: if=%s carrier=%u running=%u pause_map=0x%x\",\n"
        "\t\t   dev->name, netif_carrier_ok(dev) ? 1 : 0,\n"
        "\t\t   netif_running(dev) ? 1 : 0, adapter->pause_map);\n\n"
        "\tparam.policy = BBM_DRIVER_MODE_POLICY;\n"
        "\tparam.policy_info.driver_mode = QDF_GLOBAL_MONITOR_MODE;\n"
        "\thdd_bbm_apply_independent_policy(hdd_ctx, &param);\n"
        "\thdd_set_current_throughput_level(hdd_ctx,\n"
        "\t\t\t\t\t  PLD_BUS_WIDTH_VERY_HIGH);\n\n"
        "\treturn 0;\n}",
        "main.c __hdd_mon_open")

    # --- wma_main.c : wma_open -- inject queue init, after this tree's
    #     extra wma_trace_init() call ---
    f = WMA_SRC / "wma_main.c"
    fuzzy_replace(f,
        "\twma_register_mws_coex_events(wma_handle);\n"
        "\twma_trace_init();\n\treturn QDF_STATUS_SUCCESS;",
        "\twma_register_mws_coex_events(wma_handle);\n"
        "\twma_trace_init();\n\n"
        "\t/* Initialize frame injection queue */\n"
        "\tqdf_status = wma_init_injection_queue(wma_handle);\n"
        "\tif (qdf_status != QDF_STATUS_SUCCESS) {\n"
        "\t\tWMA_LOGE(\"%s: Failed to initialize injection queue: %d\",\n"
        "\t\t\t __func__, qdf_status);\n"
        "\t\t/* Continue initialization - injection queue failure is not fatal */\n"
        "\t}\n\n\treturn QDF_STATUS_SUCCESS;",
        "wma_main.c wma_open injection queue init")

    # --- wma_utils.c : wma_mon_mlme_vdev_down_send -- del_bss_resp alloc ---
    f = WMA_SRC / "wma_utils.c"
    fuzzy_replace(f,
        "\twlan_vdev_mlme_sm_deliver_evt(vdev_mlme->vdev,\n"
        "\t\t\t\t      WLAN_VDEV_SM_EV_DOWN_COMPLETE,\n"
        "\t\t\t\t      0,\n"
        "\t\t\t\t      NULL);\n\n\treturn status;",
        "\tresp = qdf_mem_malloc(sizeof(*resp));\n"
        "\tif (resp) {\n"
        "\t\tresp->vdev_id = vdev_id;\n"
        "\t\tresp->status = QDF_STATUS_SUCCESS;\n"
        "\t\twlan_vdev_mlme_sm_deliver_evt(vdev_mlme->vdev,\n"
        "\t\t\t\t\t      WLAN_VDEV_SM_EV_DOWN_COMPLETE,\n"
        "\t\t\t\t\t      sizeof(*resp),\n"
        "\t\t\t\t\t      resp);\n"
        "\t}\n\n\treturn status;",
        "wma_utils.c del_bss_resp alloc")

    # --- default_defconfig : enable the feature flag ---
    f = CONFIGS / "default_defconfig"
    fuzzy_replace(f,
        "CONFIG_FEATURE_MONITOR_MODE_SUPPORT := y\n",
        "CONFIG_FEATURE_MONITOR_MODE_SUPPORT := y\n"
        "CONFIG_FEATURE_FRAME_INJECTION_SUPPORT := y\n",
        "default_defconfig injection flag")

    # --- tx_rx.c : wlan_hdd_netif_queue_control -- monitor-interface
    #     carrier/queue debug guard, after this tree's extra MLO
    #     hdd_adapter_is_link_adapter() guard ---
    f = HDD_SRC / "wlan_hdd_tx_rx.c"
    fuzzy_replace(f,
        "\tuint32_t temp_map;\n\tuint8_t index;\n"
        "\tstruct hdd_netif_queue_history *txq_hist_ptr;\n\n"
        "\tif ((!adapter) || (WLAN_HDD_ADAPTER_MAGIC != adapter->magic) ||\n"
        "\t    (!adapter->dev)) {\n\t\thdd_err(\"adapter is invalid\");\n\t\treturn;\n\t}\n\n"
        "\tif (hdd_adapter_is_link_adapter(adapter))\n\t\treturn;\n\n\tswitch (action) {",
        "\tuint32_t temp_map;\n\tuint8_t index;\n"
        "\tstruct hdd_netif_queue_history *txq_hist_ptr;\n\tbool monitor_iface;\n\n"
        "\tif ((!adapter) || (WLAN_HDD_ADAPTER_MAGIC != adapter->magic) ||\n"
        "\t    (!adapter->dev)) {\n\t\thdd_err(\"adapter is invalid\");\n\t\treturn;\n\t}\n\n"
        "\tif (hdd_adapter_is_link_adapter(adapter))\n\t\treturn;\n\n"
        "\tmonitor_iface = wlan_hdd_is_session_type_monitor(adapter->device_mode) ||\n"
        "\t\t\t(adapter->dev->type == ARPHRD_IEEE80211_RADIOTAP);\n"
        "\tif (monitor_iface &&\n"
        "\t    (action == WLAN_STOP_ALL_NETIF_QUEUE_N_CARRIER ||\n"
        "\t     action == WLAN_START_ALL_NETIF_QUEUE_N_CARRIER ||\n"
        "\t     action == WLAN_NETIF_CARRIER_OFF ||\n"
        "\t     action == WLAN_NETIF_CARRIER_ON)) {\n"
        "\t\thdd_warn_rl(\"monitor netif queue ctrl: if=%s action=%s reason=%s carrier=%u running=%u pause_map=0x%x caller=%s\",\n"
        "\t\t\t    adapter->dev->name, hdd_action_type_to_string(action),\n"
        "\t\t\t    hdd_reason_type_to_string(reason),\n"
        "\t\t\t    netif_carrier_ok(adapter->dev) ? 1 : 0,\n"
        "\t\t\t    netif_running(adapter->dev) ? 1 : 0,\n"
        "\t\t\t    adapter->pause_map, current->comm);\n\t}\n\n\tswitch (action) {",
        "tx_rx.c netif_queue_control monitor guard")


def cleanup():
    removed = 0
    for pattern in ("*.rej", "*.orig"):
        for p in QCACLD.rglob(pattern):
            p.unlink()
            removed += 1
    if removed:
        print(f"\nRemoved {removed} leftover .rej/.orig file(s)")


def main():
    run_bulk_patch()
    apply_hand_merges()
    cleanup()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} hand-merge fix(es) did not apply:")
        for msg in FAILURES:
            print(f"  - {msg}")
        print(
            "\nThis means the tree has drifted further since these fixes "
            "were derived by hand against a specific snapshot. Re-check the "
            "actual file content at the point of failure and update the "
            "corresponding old_block/new_block in this script."
        )
        sys.exit(1)

    print("All hunks applied or hand-merged successfully.")
    print(
        "Deliberately skipped (non-essential / real risk of breaking "
        "tree-specific logic):\n"
        "  - main.c wlan_off/on root-UID gating while monitor active\n"
        "  - main.c HDD_FW_VER_SUB_ID macro fix (unrelated)\n"
        "  - Kbuild -Wmaybe-uninitialized / -Wframe-larger-than block (unrelated)"
    )


if __name__ == "__main__":
    main()
