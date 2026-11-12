// The same personalized binary is installed as bootx64.efi and normalx64.efi.
// Only an exact Debug basename opts in; unavailable or ambiguous metadata keeps
// normal policy. This file is compiled into firmware and directly host-tested.
extern crate alloc;

use alloc::string::String;
use alloc::vec::Vec;

pub enum PathNode {
    File(Vec<u16>),
    Other,
}

pub fn debug_boot(path: Result<Option<Vec<PathNode>>, ()>) -> bool {
    let Ok(Some(nodes)) = path else {
        return false;
    };
    let mut name = Vec::new();
    for node in nodes {
        match node {
            PathNode::File(part) => {
                let Some(part) = part.strip_suffix(&[0]) else {
                    return false;
                };
                if part.is_empty()
                    || part
                        .iter()
                        .any(|c| *c == 0 || (0xd800..=0xdfff).contains(c))
                {
                    return false;
                }
                // UEFI joins separate file nodes with a separator when needed;
                // only the final file node can contain the executable basename.
                name.clear();
                name.extend_from_slice(part);
            }
            PathNode::Other if !name.is_empty() => return false,
            PathNode::Other => {}
        }
    }
    let basename = name
        .rsplit(|c| *c == u16::from(b'\\') || *c == u16::from(b'/'))
        .next()
        .unwrap_or(&[]);
    let expected = b"bootx64.efi";
    basename.len() == expected.len()
        && basename
            .iter()
            .zip(expected)
            .all(|(a, b)| *a == u16::from(*b) || *a == u16::from(b.to_ascii_uppercase()))
}

pub fn cmdline(kernel_cmdline: &str, debug_boot: bool) -> String {
    // Keep official kernel verification intact. Enable developer mounts and a
    // separate writable overlay without modifying the verified root partition.
    let mut kernel_cmdline = kernel_cmdline
        .split_whitespace()
        .take_while(|arg| *arg != "--")
        .filter(|arg| *arg != "cros_nodebug" && *arg != "cros_debug" && !arg.starts_with("init="))
        .collect::<alloc::vec::Vec<_>>()
        .join(" ");
    // Linux next_arg accepts one double-quoted init argument, but cannot escape
    // embedded double quotes. Use only single quotes inside the shell program.
    // The EXIT trap always restores stock PID 1 if the optional script fails.
    kernel_cmdline.push_str(if debug_boot {
        " cros_debug"
    } else {
        " cros_nodebug"
    });
    kernel_cmdline.push_str(concat!(
        " chromiumos.allow_overlayfs init=/bin/sh -- -c \"",
        "PATH=/sbin:/bin:/usr/sbin:/usr/bin;export PATH;",
        "mount -t proc -o nosuid,nodev,noexec proc /proc;",
        "mount -t sysfs -o nosuid,nodev,noexec sysfs /sys;",
        "mount -t devtmpfs -o nosuid,noexec devtmpfs /dev;",
        "mount -t tmpfs -o nosuid,nodev,noexec,nosymfollow,mode=1777 tmpfs /tmp;",
        "mkdir -p /tmp/flex-debug-esp;",
        "trap 'umount -l /tmp/flex-debug-esp 2>/dev/null;exec /sbin/init' EXIT;",
        "d=$(rootdev -s -d);case $d in *[0-9])p=${d}p12;;*)p=${d}12;;esac;",
        "FLEX_DEBUG_INIT_SOURCE=/tmp/flex-debug-esp/EFI/cros-flex-debug/rw-init.sh;",
        "export FLEX_DEBUG_INIT_SOURCE;",
        "if mount -t vfat -o ro,nosuid,nodev $p /tmp/flex-debug-esp && ",
        "[ -r $FLEX_DEBUG_INIT_SOURCE ];then . $FLEX_DEBUG_INIT_SOURCE;fi\"",
    ));

    kernel_cmdline
}

#[cfg(test)]
mod tests {
    use super::*;

    fn path(parts: &[&str]) -> Result<Option<Vec<PathNode>>, ()> {
        Ok(Some(
            parts
                .iter()
                .map(|part| {
                    PathNode::File(part.encode_utf16().chain(core::iter::once(0)).collect())
                })
                .collect(),
        ))
    }

    fn flag(metadata: Result<Option<Vec<PathNode>>, ()>) -> String {
        cmdline(
            "cros_efi cros_debug cros_nodebug init=/old root=PARTUUID=x -- old-init-args",
            debug_boot(metadata),
        )
    }

    fn assert_policy(metadata: Result<Option<Vec<PathNode>>, ()>, expected: &str) {
        let line = flag(metadata);
        let args: Vec<_> = line
            .split_whitespace()
            .take_while(|arg| *arg != "--")
            .collect();
        assert_eq!(
            args.iter()
                .filter(|arg| matches!(**arg, "cros_debug" | "cros_nodebug"))
                .copied()
                .collect::<Vec<_>>(),
            vec![expected]
        );
        assert_eq!(
            args.iter()
                .filter(|arg| arg.starts_with("init="))
                .copied()
                .collect::<Vec<_>>(),
            vec!["init=/bin/sh"]
        );
        assert!(args.contains(&"cros_efi"));
        assert!(args.contains(&"root=PARTUUID=x"));
        assert!(args.contains(&"chromiumos.allow_overlayfs"));
        assert!(!line.contains("old-init-args"));
    }

    #[test]
    fn only_exact_debug_basename_enables_debug() {
        for name in [
            r"\EFI\cros-flex-debug\bootx64.efi",
            "BOOTX64.EFI",
            "/EFI/cros-flex-debug/BootX64.Efi",
        ] {
            assert_policy(path(&[name]), "cros_debug");
        }
        for name in [
            "",
            "normalx64.efi",
            "renamed.efi",
            "xbootx64.efi",
            "bootx64.efi.old",
            "bootx64.efi/",
            "bootx64xefi",
            "bootx64.efi\0evil",
            "böotx64.efi",
            r"bootx64.efi\normalx64.efi",
        ] {
            assert_policy(path(&[name]), "cros_nodebug");
        }
    }

    #[test]
    fn final_file_node_owns_the_basename() {
        assert_policy(
            path(&[r"\EFI\cros-flex-debug\", "bootx64.efi"]),
            "cros_debug",
        );
        assert_policy(path(&[r"\EFI\", "bootx64", ".efi"]), "cros_nodebug");
        assert_policy(path(&["normalx64.efi", "bootx64.efi"]), "cros_debug");
        assert_policy(
            path(&[r"\EFI\cros-flex-debug", "bootx64.efi"]),
            "cros_debug",
        );
        assert_policy(
            path(&[r"\EFI\cros-flex-debug\", r"\bootx64.efi"]),
            "cros_debug",
        );
        assert_policy(path(&["bootx64.efi", "normalx64.efi"]), "cros_nodebug");
    }

    #[test]
    fn missing_or_failed_metadata_keeps_normal_policy() {
        assert_policy(Err(()), "cros_nodebug");
        assert_policy(Ok(None), "cros_nodebug");
        assert_policy(Ok(Some(Vec::new())), "cros_nodebug");
        assert_policy(Ok(Some(vec![PathNode::Other])), "cros_nodebug");
    }

    #[test]
    fn malformed_or_ambiguous_file_nodes_keep_normal_policy() {
        let file: Vec<u16> = "bootx64.efi".encode_utf16().collect();
        assert_policy(Ok(Some(vec![PathNode::File(file.clone())])), "cros_nodebug");
        let mut invalid = vec![0xd800, u16::from(b'\\')];
        invalid.extend(file.iter().copied());
        invalid.push(0);
        assert_policy(Ok(Some(vec![PathNode::File(invalid)])), "cros_nodebug");
        let mut valid = file;
        valid.push(0);
        assert_policy(
            Ok(Some(vec![PathNode::File(valid), PathNode::Other])),
            "cros_nodebug",
        );
    }
}
