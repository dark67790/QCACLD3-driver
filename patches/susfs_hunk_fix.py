#!/usr/bin/env python3
# Fixes Samsung-specific rejected hunks from 50_add_susfs_in_gki-android12-5.10.patch
# and wild/a12-5.10/base.c.patch
# Run from kernel_platform/common/ AFTER both patches have been applied

import re

def fix(path, old, new, label):
    with open(path, 'r') as f:
        s = f.read()
    if old not in s:
        print(f"⚠️  {label}: context not found — already applied or source changed")
        return
    with open(path, 'w') as f:
        f.write(s.replace(old, new, 1))
    print(f"✅ {label}")

# Fix 1: fs/exec.c — add susfs_def.h after io_uring.h
fix('fs/exec.c',
    '#include <linux/io_uring.h>',
    '#include <linux/io_uring.h>\n#ifdef CONFIG_KSU_SUSFS\n#include <linux/susfs_def.h>\n#endif',
    'fs/exec.c')

# Fix 2: fs/namespace.c — include block after shmem_fs.h
fix('fs/namespace.c',
    '#include <linux/shmem_fs.h>',
    '#include <linux/shmem_fs.h>\n#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n#include <linux/susfs_def.h>\n#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MOUNT',
    'fs/namespace.c include')

# Fix 3: fs/namespace.c — extern declarations before sysctl_mount_max
fix('fs/namespace.c',
    '/* Maximum number of mounts in a mount namespace */',
    '#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\nextern bool susfs_is_current_ksu_domain(void);\nextern struct static_key_true susfs_is_sdcard_android_data_not_decrypted;\n\n#define CL_COPY_MNT_NS BIT(25) /* used by copy_mnt_ns() */\n\n#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MOUNT\n\n/* Maximum number of mounts in a mount namespace */',
    'fs/namespace.c externs')

# Fix 4: fs/proc/base.c — include after cpufreq_times.h
fix('fs/proc/base.c',
    '#include <linux/cpufreq_times.h>',
    '#include <linux/cpufreq_times.h>\n#if defined(CONFIG_KSU_SUSFS_SUS_MAP) || defined(CONFIG_KSU_SUSFS_OPEN_REDIRECT)\n#include <linux/susfs_def.h>\n#endif // #if defined(CONFIG_KSU_SUSFS_SUS_MAP) || defined(CONFIG_KSU_SUSFS_OPEN_REDIRECT)\n',
    'fs/proc/base.c include')

# Fix 5: fs/proc/base.c — vma undeclared from wild base.c patch
# Regex handles any whitespace variant
def fix_vma_declaration(path):
    with open(path, 'r') as f:
        lines = f.readlines()
    changed = False
    prev_was_susfs_ifdef = False
    result = []
    for line in lines:
        if prev_was_susfs_ifdef and 'vma = find_vma' in line and 'struct' not in line:
            line = line.replace('vma = find_vma', 'struct vm_area_struct *vma = find_vma')
            changed = True
        prev_was_susfs_ifdef = '#ifdef CONFIG_KSU_SUSFS_SUS_MAP' in line
        result.append(line)
    if changed:
        with open(path, 'w') as f:
            f.writelines(result)
        print("✅ fs/proc/base.c vma declaration")
    else:
        print("⚠️  fs/proc/base.c vma declaration: not found")

fix_vma_declaration('fs/proc/base.c')

print("\n✅ All susfs hunk fixes done")
