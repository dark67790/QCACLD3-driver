import re

path = "include/linux/lockdep.h"
with open(path, "r") as f:
    content = f.read()

old = (
    "#define lockdep_assert_held(l)\tdo {\t\t\t\t\\\n"
    "\t\tWARN_ON(debug_locks && !lockdep_is_held(l));\t\\\n"
    "\t} while (0)\n"
    "\n"
    "#define lockdep_assert_held_write(l)\tdo {\t\t\t\\\n"
    "\t\tWARN_ON(debug_locks && !lockdep_is_held_type(l, 0));\t\\\n"
    "\t} while (0)\n"
    "\n"
    "#define lockdep_assert_held_read(l)\tdo {\t\t\t\t\\\n"
    "\t\tWARN_ON(debug_locks && !lockdep_is_held_type(l, 1));\t\\\n"
    "\t} while (0)\n"
    "\n"
    "#define lockdep_assert_held_once(l)\tdo {\t\t\t\t\\\n"
    "\t\tWARN_ON_ONCE(debug_locks && !lockdep_is_held(l));\t\\\n"
    "\t} while (0)\n"
)

new = (
    "#define lockdep_assert(cond)\t\t\\\n"
    "\tdo { WARN_ON(debug_locks && !(cond)); } while (0)\n"
    "\n"
    "#define lockdep_assert_once(cond)\t\\\n"
    "\tdo { WARN_ON_ONCE(debug_locks && !(cond)); } while (0)\n"
    "\n"
    "#define lockdep_assert_held(l)\t\t\\\n"
    "\tlockdep_assert(lockdep_is_held(l) != LOCK_STATE_NOT_HELD)\n"
    "\n"
    "#define lockdep_assert_not_held(l)\t\\\n"
    "\tlockdep_assert(lockdep_is_held(l) != LOCK_STATE_HELD)\n"
    "\n"
    "#define lockdep_assert_held_write(l)\t\\\n"
    "\tlockdep_assert(lockdep_is_held_type(l, 0))\n"
    "\n"
    "#define lockdep_assert_held_read(l)\t\\\n"
    "\tlockdep_assert(lockdep_is_held_type(l, 1))\n"
    "\n"
    "#define lockdep_assert_held_once(l)\t\t\\\n"
    "\tlockdep_assert_once(lockdep_is_held(l) != LOCK_STATE_NOT_HELD)\n"
    "\n"
    "#define lockdep_assert_none_held_once()\t\t\\\n"
    "\tlockdep_assert_once(!current->lockdep_depth)\n"
)

if old in content:
    content = content.replace(old, new)
    with open(path, "w") as f:
        f.write(content)
    print("✅ ntsync lockdep.h fix applied")
else:
    print("❌ lockdep.h target block not found — check Samsung source version")
    exit(1)
