#!/usr/bin/env python3
# Run from kernel_platform/common/drivers/kernelsu/

import re
import os

def fix(path, old, new, label):
    if not os.path.exists(path):
        print(f"❌ {label}: {path} not found")
        return
    with open(path, 'r') as f:
        s = f.read()
    if old not in s:
        print(f"⚠️  {label}: context not found — already applied or source changed")
        return
    with open(path, 'w') as f:
        f.write(s.replace(old, new, 1))
    print(f"✅ {label}")

# --- Fix 1 & 4: core/init.c ---
path_init = 'core/init.c'
if os.path.exists(path_init):
    with open(path_init, 'r') as f:
        s = f.read()
    original = s

    # Fix 1: Remove ksu_late_loaded (rejected by 10_enable patch)
    s = s.replace('bool ksu_late_loaded;\n\n', '')
    s = s.replace('bool ksu_late_loaded;\n', '')
    s = re.sub(
        r'#if defined\(__x86_64__\)\n.*?#endif\n\n#ifdef MODULE\n\tksu_late_loaded = \(current->pid != 1\);\n#else\n\tksu_late_loaded = false;\n#endif\n\n',
        '',
        s,
        flags=re.DOTALL
    )

    # Fix 4: Add missing includes for sucompat and setuid hook
    if 'ksu_sucompat_init' in s and '"feature/sucompat.h"' not in s:
        if '#include "policy/allowlist.h"' in s:
            s = s.replace('#include "policy/allowlist.h"', '#include "policy/allowlist.h"\n#include "feature/sucompat.h"')
            print("✅ core/init.c: sucompat.h include added")
        else:
            # Fallback if allowlist.h isn't there
            s = s.replace('#include "ksu.h"', '#include "ksu.h"\n#include "feature/sucompat.h"')

    if 'ksu_setuid_hook_init' in s and '"hook/setuid_hook.h"' not in s:
        if '#include "policy/allowlist.h"' in s:
            s = s.replace('#include "policy/allowlist.h"', '#include "policy/allowlist.h"\n#include "hook/setuid_hook.h"')
            print("✅ core/init.c: setuid_hook.h include added")
        else:
            s = s.replace('#include "ksu.h"', '#include "ksu.h"\n#include "hook/setuid_hook.h"')

    if s != original:
        with open(path_init, 'w') as f:
            f.write(s)
        print("✅ core/init.c: Cleanup and Includes applied")
    else:
        print("⚠️  core/init.c: No changes needed")
else:
    print("❌ core/init.c: File not found")


# --- Fix 2: supercall/supercall.c ---
# ksys_close undeclared on kernel < 5.11
fix('supercall/supercall.c',
    '#include <linux/version.h>',
    '#include <linux/version.h>\n#include <linux/syscalls.h>',
    'supercall.c ksys_close header')


# --- Fix 3: selinux/selinux.c ---
# Define fake_state and ksu_selinux_hide_running (needed by hooks.c and selinuxfs.c)
fix('selinux/selinux.c',
    '#include "ksu.h"',
    '#include "ksu.h"\n#include "security.h"\n\nbool ksu_selinux_hide_running __read_mostly = false;\nstruct selinux_state fake_state;',
    'selinux/selinux.c fake_state + ksu_selinux_hide_running')


# --- Fix 5: hook/setuid_hook.c ---
# define missing functions that fix_setuid_hook.c.patch requires
fix('hook/setuid_hook.c',
    '#include <linux/susfs_def.h>',
    '''#include <linux/susfs_def.h>

static inline bool is_zygote_normal_app_uid(uid_t uid)
{
\treturn uid >= 10000 && uid < 99999;
}

static inline void ksu_handle_extra_susfs_work(void)
{
\t/* SUSFS extra work handled via susfs_set_current_proc_umounted */
}''',
    'setuid_hook.c missing function definitions')


print("\n✅ All ksun fixes done")
