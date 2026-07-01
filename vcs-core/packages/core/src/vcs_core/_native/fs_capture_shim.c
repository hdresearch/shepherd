#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

typedef struct {
    int active;
    int dirty;
    int write_observed;
    char path[PATH_MAX];
    char owner_command_operation_id[PATH_MAX];
    char owner_capture_epoch[PATH_MAX];
} fd_state_t;

typedef enum {
    FCNTL_ARG_NONE = 0,
    FCNTL_ARG_INT = 1,
    FCNTL_ARG_PTR = 2
} fcntl_arg_kind_t;

static fd_state_t fd_states[4096];
static char workspace_root[PATH_MAX];
static char socket_path[PATH_MAX];
static char scope_name[PATH_MAX];
static char scope_instance_id[PATH_MAX];
static char command_operation_id[PATH_MAX];
static char capture_epoch[PATH_MAX];
static char debug_log_path[PATH_MAX];
static int capture_socket_fd = -1;
static unsigned long proc_seq = 0;

static int (*real_open_fn)(const char *, int, ...) = NULL;
static int (*real_open64_fn)(const char *, int, ...) = NULL;
static int (*real_openat_fn)(int, const char *, int, ...) = NULL;
static int (*real_openat64_fn)(int, const char *, int, ...) = NULL;
static int (*real_creat_fn)(const char *, mode_t) = NULL;
static ssize_t (*real_write_fn)(int, const void *, size_t) = NULL;
static ssize_t (*real_writev_fn)(int, const struct iovec *, int) = NULL;
static ssize_t (*real_pwrite_fn)(int, const void *, size_t, off_t) = NULL;
static int (*real_close_fn)(int) = NULL;
static int (*real_dup_fn)(int) = NULL;
static int (*real_dup2_fn)(int, int) = NULL;
static int (*real_dup3_fn)(int, int, int) = NULL;
static int (*real_fcntl_fn)(int, int, ...) = NULL;
static int (*real_unlink_fn)(const char *) = NULL;
static int (*real_unlinkat_fn)(int, const char *, int) = NULL;
static int (*real_chmod_fn)(const char *, mode_t) = NULL;
static int (*real_fchmod_fn)(int, mode_t) = NULL;
static int (*real_fchmodat_fn)(int, const char *, mode_t, int) = NULL;

static void resolve_symbols(void);
static void clear_fd(int fd);
static void remember_fd(int fd, const char *path, int flags);
static void clone_fd_state(int oldfd, int newfd);
static void mark_fd_written(int fd);
static fcntl_arg_kind_t fcntl_arg_kind(int cmd);
static void emit_path_event(const char *op, const char *path);
static void emit_path_event_with_context(const char *op, const char *path, const char *command_id, const char *epoch);
static void emit_shell_command_finish_event(void);
static void emit_lifecycle_event(const char *phase, const char *lifecycle);
static int capture_context_enabled(void);
static int capture_suppressed(void);
static int load_capture_context(char *command_id, size_t command_len, char *epoch, size_t epoch_len);
static int env_flag_enabled(const char *name);
static int resolve_candidate_path(int dirfd, const char *path, char *out, size_t out_len);
static int resolve_existing_rel_path(int dirfd, const char *path, char *out, size_t out_len);
static int resolve_unlink_rel_path(int dirfd, const char *path, char *out, size_t out_len);
static int resolve_fd_rel_path(int fd, char *out, size_t out_len);
static int maybe_emit_shell_finish_for_fd(int fd);
static int json_escape(const char *src, char *out, size_t out_len);
static void invalidate_capture_socket(void);
static void debug_log(const char *message, const char *path);

static void resolve_symbols(void) {
    if (real_open_fn != NULL) {
        return;
    }
    real_open_fn = dlsym(RTLD_NEXT, "open");
    real_open64_fn = dlsym(RTLD_NEXT, "open64");
    real_openat_fn = dlsym(RTLD_NEXT, "openat");
    real_openat64_fn = dlsym(RTLD_NEXT, "openat64");
    real_creat_fn = dlsym(RTLD_NEXT, "creat");
    real_write_fn = dlsym(RTLD_NEXT, "write");
    real_writev_fn = dlsym(RTLD_NEXT, "writev");
    real_pwrite_fn = dlsym(RTLD_NEXT, "pwrite");
    real_close_fn = dlsym(RTLD_NEXT, "close");
    real_dup_fn = dlsym(RTLD_NEXT, "dup");
    real_dup2_fn = dlsym(RTLD_NEXT, "dup2");
    real_dup3_fn = dlsym(RTLD_NEXT, "dup3");
    real_fcntl_fn = dlsym(RTLD_NEXT, "fcntl");
    real_unlink_fn = dlsym(RTLD_NEXT, "unlink");
    real_unlinkat_fn = dlsym(RTLD_NEXT, "unlinkat");
    real_chmod_fn = dlsym(RTLD_NEXT, "chmod");
    real_fchmod_fn = dlsym(RTLD_NEXT, "fchmod");
    real_fchmodat_fn = dlsym(RTLD_NEXT, "fchmodat");
}

static int env_flag_enabled(const char *name) {
    const char *value = getenv(name);
    return value != NULL && strcmp(value, "1") == 0;
}

static int capture_suppressed(void) {
    return env_flag_enabled("VCS_CORE_FS_CAPTURE_SUPPRESS");
}

static int capture_context_enabled(void) {
    char command_id[PATH_MAX];
    char epoch[PATH_MAX];
    return load_capture_context(command_id, sizeof(command_id), epoch, sizeof(epoch));
}

static int load_capture_context(char *command_id, size_t command_len, char *epoch, size_t epoch_len) {
    const char *command_operation = getenv("VCS_CORE_COMMAND_OPERATION_ID");
    const char *capture_epoch_env = getenv("VCS_CORE_CAPTURE_EPOCH");
    if (capture_suppressed()) {
        return 0;
    }
    if (!env_flag_enabled("VCS_CORE_CAPTURE_ACTIVE")) {
        return 0;
    }
    if (command_operation == NULL || command_operation[0] == '\0' ||
        capture_epoch_env == NULL || capture_epoch_env[0] == '\0') {
        return 0;
    }
    snprintf(command_id, command_len, "%s", command_operation);
    snprintf(epoch, epoch_len, "%s", capture_epoch_env);
    if (strcmp(command_operation_id, command_id) != 0 || strcmp(capture_epoch, epoch) != 0) {
        snprintf(command_operation_id, sizeof(command_operation_id), "%s", command_id);
        snprintf(capture_epoch, sizeof(capture_epoch), "%s", epoch);
        proc_seq = 0;
    }
    return 1;
}

__attribute__((constructor))
static void init_capture(void) {
    const char *workspace = getenv("VCS_CORE_WORKSPACE");
    const char *sock = getenv("VCS_CORE_HOOK_SOCKET");
    const char *scope = getenv("VCS_CORE_SCOPE");
    const char *scope_instance = getenv("VCS_CORE_SCOPE_INSTANCE_ID");
    const char *command_operation = getenv("VCS_CORE_COMMAND_OPERATION_ID");
    const char *epoch = getenv("VCS_CORE_CAPTURE_EPOCH");
    const char *debug_log_env = getenv("VCS_CORE_FS_CAPTURE_DEBUG_LOG");
    char resolved_workspace[PATH_MAX];
    resolve_symbols();
    if (workspace != NULL) {
        if (realpath(workspace, resolved_workspace) != NULL) {
            snprintf(workspace_root, sizeof(workspace_root), "%s", resolved_workspace);
        } else {
            snprintf(workspace_root, sizeof(workspace_root), "%s", workspace);
        }
    }
    if (sock != NULL) {
        snprintf(socket_path, sizeof(socket_path), "%s", sock);
    }
    if (scope != NULL) {
        snprintf(scope_name, sizeof(scope_name), "%s", scope);
    }
    if (scope_instance != NULL) {
        snprintf(scope_instance_id, sizeof(scope_instance_id), "%s", scope_instance);
    }
    if (command_operation != NULL) {
        snprintf(command_operation_id, sizeof(command_operation_id), "%s", command_operation);
    }
    if (epoch != NULL) {
        snprintf(capture_epoch, sizeof(capture_epoch), "%s", epoch);
    }
    if (debug_log_env != NULL) {
        snprintf(debug_log_path, sizeof(debug_log_path), "%s", debug_log_env);
    }
    debug_log("init", NULL);
    emit_lifecycle_event("start", "process_start");
}

__attribute__((destructor))
static void flush_dirty_fds_on_exit(void) {
    int i;
    for (i = 0; i < (int)(sizeof(fd_states) / sizeof(fd_states[0])); i++) {
        if (fd_states[i].active && fd_states[i].dirty && fd_states[i].write_observed) {
            emit_path_event_with_context(
                "write_close",
                fd_states[i].path,
                fd_states[i].owner_command_operation_id,
                fd_states[i].owner_capture_epoch
            );
            clear_fd(i);
        }
    }
    emit_lifecycle_event("finish", "process_finish");
    if (capture_socket_fd >= 0) {
        invalidate_capture_socket();
    }
    debug_log("destructor", NULL);
}

static int path_is_ignored(const char *rel) {
    if (rel == NULL || rel[0] == '\0') {
        return 1;
    }
    if (strcmp(rel, ".vcscore") == 0) {
        return 1;
    }
    if (strncmp(rel, ".vcscore/", 9) == 0) {
        return 1;
    }
    return 0;
}

static int relativize_path(const char *abs_path, char *out, size_t out_len) {
    size_t root_len;
    const char *suffix;
    if (workspace_root[0] == '\0' || abs_path == NULL) {
        return 0;
    }
    root_len = strlen(workspace_root);
    if (strncmp(abs_path, workspace_root, root_len) != 0) {
        return 0;
    }
    suffix = abs_path + root_len;
    if (*suffix != '\0' && *suffix != '/') {
        return 0;
    }
    if (*suffix == '/') {
        suffix += 1;
    }
    if (*suffix == '\0') {
        return 0;
    }
    snprintf(out, out_len, "%s", suffix);
    return path_is_ignored(out) == 0;
}

static int resolve_base_dir(int dirfd, char *out, size_t out_len) {
    char proc_path[64];
    char link_target[PATH_MAX];
    ssize_t read_len;
    if (dirfd == AT_FDCWD) {
        return getcwd(out, out_len) != NULL;
    }
    snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", dirfd);
    read_len = readlink(proc_path, link_target, sizeof(link_target) - 1);
    if (read_len < 0) {
        return 0;
    }
    link_target[read_len] = '\0';
    snprintf(out, out_len, "%s", link_target);
    return 1;
}

static int resolve_candidate_path(int dirfd, const char *path, char *out, size_t out_len) {
    char base[PATH_MAX];
    if (path == NULL || path[0] == '\0') {
        return 0;
    }
    if (path[0] == '/') {
        snprintf(out, out_len, "%s", path);
        return 1;
    }
    if (!resolve_base_dir(dirfd, base, sizeof(base))) {
        return 0;
    }
    if (snprintf(out, out_len, "%s/%s", base, path) >= (int)out_len) {
        return 0;
    }
    return 1;
}

static int resolve_existing_rel_path(int dirfd, const char *path, char *out, size_t out_len) {
    char candidate[PATH_MAX];
    char resolved[PATH_MAX];
    if (!resolve_candidate_path(dirfd, path, candidate, sizeof(candidate))) {
        return 0;
    }
    if (realpath(candidate, resolved) == NULL) {
        return 0;
    }
    return relativize_path(resolved, out, out_len);
}

static int resolve_unlink_rel_path(int dirfd, const char *path, char *out, size_t out_len) {
    char candidate[PATH_MAX];
    char parent[PATH_MAX];
    char resolved_parent[PATH_MAX];
    char entry_path[PATH_MAX];
    char *leaf;
    size_t parent_len;
    if (!resolve_candidate_path(dirfd, path, candidate, sizeof(candidate))) {
        return 0;
    }
    leaf = strrchr(candidate, '/');
    if (leaf == NULL || leaf[1] == '\0') {
        return 0;
    }
    parent_len = (size_t)(leaf - candidate);
    if (parent_len == 0) {
        snprintf(parent, sizeof(parent), "/");
    } else if (parent_len >= sizeof(parent)) {
        return 0;
    } else {
        memcpy(parent, candidate, parent_len);
        parent[parent_len] = '\0';
    }
    if (realpath(parent, resolved_parent) == NULL) {
        return 0;
    }
    if (snprintf(entry_path, sizeof(entry_path), "%s/%s", resolved_parent, leaf + 1) >= (int)sizeof(entry_path)) {
        return 0;
    }
    return relativize_path(entry_path, out, out_len);
}

static int resolve_fd_rel_path(int fd, char *out, size_t out_len) {
    char proc_path[64];
    char link_target[PATH_MAX];
    ssize_t read_len;
    if (fd < 0) {
        return 0;
    }
    snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", fd);
    read_len = readlink(proc_path, link_target, sizeof(link_target) - 1);
    if (read_len < 0) {
        return 0;
    }
    link_target[read_len] = '\0';
    return relativize_path(link_target, out, out_len);
}

static int maybe_emit_shell_finish_for_fd(int fd) {
    const char *trigger_path;
    char proc_path[64];
    char link_target[PATH_MAX];
    ssize_t read_len;
    if (!env_flag_enabled("VCS_CORE_SHELL_FINISH_ACTIVE")) {
        return 0;
    }
    trigger_path = getenv("VCS_CORE_SHELL_FINISH_PATH");
    if (trigger_path == NULL || trigger_path[0] == '\0' || fd < 0) {
        return 0;
    }
    snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", fd);
    read_len = readlink(proc_path, link_target, sizeof(link_target) - 1);
    if (read_len < 0) {
        return 0;
    }
    link_target[read_len] = '\0';
    if (strcmp(link_target, trigger_path) != 0) {
        return 0;
    }
    emit_shell_command_finish_event();
    return 1;
}

static int ensure_capture_socket(void) {
    struct sockaddr_un addr;
    int high_fd;
    if (capture_socket_fd >= 0) {
        return 1;
    }
    if (socket_path[0] == '\0' || scope_name[0] == '\0') {
        return 0;
    }
    capture_socket_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (capture_socket_fd < 0) {
        return 0;
    }
    high_fd = real_fcntl_fn(capture_socket_fd, F_DUPFD_CLOEXEC, 1024);
    if (high_fd >= 0) {
        real_close_fn(capture_socket_fd);
        capture_socket_fd = high_fd;
    }
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", socket_path);
    if (connect(capture_socket_fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        debug_log("connect_failed", socket_path);
        real_close_fn(capture_socket_fd);
        capture_socket_fd = -1;
        return 0;
    }
    debug_log("connect_ok", socket_path);
    return 1;
}

static int json_escape(const char *src, char *out, size_t out_len) {
    static const char hex[] = "0123456789abcdef";
    size_t write_idx = 0;
    size_t read_idx = 0;
    unsigned char ch;

    if (src == NULL || out == NULL || out_len == 0) {
        return 0;
    }

    while ((ch = (unsigned char)src[read_idx++]) != '\0') {
        const char *escape = NULL;
        char unicode_escape[7];
        size_t escape_len = 0;

        switch (ch) {
            case '\"':
                escape = "\\\"";
                escape_len = 2;
                break;
            case '\\':
                escape = "\\\\";
                escape_len = 2;
                break;
            case '\b':
                escape = "\\b";
                escape_len = 2;
                break;
            case '\f':
                escape = "\\f";
                escape_len = 2;
                break;
            case '\n':
                escape = "\\n";
                escape_len = 2;
                break;
            case '\r':
                escape = "\\r";
                escape_len = 2;
                break;
            case '\t':
                escape = "\\t";
                escape_len = 2;
                break;
            default:
                if (ch < 0x20) {
                    unicode_escape[0] = '\\';
                    unicode_escape[1] = 'u';
                    unicode_escape[2] = '0';
                    unicode_escape[3] = '0';
                    unicode_escape[4] = hex[(ch >> 4) & 0x0f];
                    unicode_escape[5] = hex[ch & 0x0f];
                    unicode_escape[6] = '\0';
                    escape = unicode_escape;
                    escape_len = 6;
                }
                break;
        }

        if (escape != NULL) {
            if (write_idx + escape_len >= out_len) {
                return 0;
            }
            memcpy(out + write_idx, escape, escape_len);
            write_idx += escape_len;
            continue;
        }

        if (write_idx + 1 >= out_len) {
            return 0;
        }
        out[write_idx++] = (char)ch;
    }

    out[write_idx] = '\0';
    return 1;
}

static void invalidate_capture_socket(void) {
    if (capture_socket_fd >= 0) {
        real_close_fn(capture_socket_fd);
        capture_socket_fd = -1;
    }
}

static void emit_path_event(const char *op, const char *path) {
    char command_id[PATH_MAX];
    char epoch[PATH_MAX];
    if (!load_capture_context(command_id, sizeof(command_id), epoch, sizeof(epoch))) {
        debug_log("emit_skipped_inactive_capture", path);
        return;
    }
    emit_path_event_with_context(op, path, command_id, epoch);
}

static void emit_path_event_with_context(const char *op, const char *path, const char *command_id, const char *epoch) {
    char escaped_scope[(PATH_MAX * 6) + 1];
    char escaped_scope_instance[(PATH_MAX * 6) + 1];
    char escaped_command_operation[(PATH_MAX * 6) + 1];
    char command_operation_json[(PATH_MAX * 6) + 16];
    char escaped_capture_epoch[(PATH_MAX * 6) + 1];
    char capture_epoch_json[(PATH_MAX * 6) + 16];
    char escaped_path[(PATH_MAX * 6) + 1];
    char payload[(PATH_MAX * 24) + 640];
    int len;
    ssize_t sent;
    unsigned long long timestamp_ns;
    if (!ensure_capture_socket()) {
        debug_log("emit_skipped_no_socket", path);
        return;
    }
    if (!json_escape(scope_name, escaped_scope, sizeof(escaped_scope)) ||
        !json_escape(scope_instance_id, escaped_scope_instance, sizeof(escaped_scope_instance)) ||
        !json_escape(path, escaped_path, sizeof(escaped_path))) {
        debug_log("emit_skipped_escape_failed", path);
        return;
    }
    if (command_id != NULL && command_id[0] != '\0') {
        if (!json_escape(command_id, escaped_command_operation, sizeof(escaped_command_operation))) {
            debug_log("emit_skipped_escape_failed", path);
            return;
        }
        snprintf(command_operation_json, sizeof(command_operation_json), "\"%s\"", escaped_command_operation);
    } else {
        snprintf(command_operation_json, sizeof(command_operation_json), "null");
    }
    if (epoch != NULL && epoch[0] != '\0') {
        if (!json_escape(epoch, escaped_capture_epoch, sizeof(escaped_capture_epoch))) {
            debug_log("emit_skipped_escape_failed", path);
            return;
        }
        snprintf(capture_epoch_json, sizeof(capture_epoch_json), "\"%s\"", escaped_capture_epoch);
    } else {
        snprintf(capture_epoch_json, sizeof(capture_epoch_json), "null");
    }
    proc_seq += 1;
    timestamp_ns = (unsigned long long)time(NULL) * 1000000000ULL;
    len = snprintf(
        payload,
        sizeof(payload),
        "{\"binding_name\":\"filesystem\",\"hook_id\":\"filesystem-direct\",\"kind\":\"ld_preload\","
        "\"phase\":\"point\",\"scope\":\"%s\",\"scope_instance_id\":\"%s\",\"pid\":%d,\"proc_seq\":%lu,"
        "\"timestamp_ns\":%llu,\"command_operation_id\":%s,\"capture_epoch\":%s,"
        "\"payload\":{\"op\":\"%s\",\"path\":\"%s\",\"seq\":%lu,"
        "\"capture_mechanism\":\"preload\"}}\n",
        escaped_scope,
        escaped_scope_instance,
        getpid(),
        proc_seq,
        timestamp_ns,
        command_operation_json,
        capture_epoch_json,
        op,
        escaped_path,
        proc_seq
    );
    if (len <= 0 || len >= (int)sizeof(payload)) {
        return;
    }
    sent = send(capture_socket_fd, payload, (size_t)len, MSG_NOSIGNAL);
    if (sent != len) {
        debug_log("send_failed", path);
        invalidate_capture_socket();
        return;
    }
    debug_log(op, path);
}

static void emit_shell_command_finish_event(void) {
    char command_id[PATH_MAX];
    char epoch[PATH_MAX];
    char escaped_scope[(PATH_MAX * 6) + 1];
    char escaped_scope_instance[(PATH_MAX * 6) + 1];
    char escaped_command_operation[(PATH_MAX * 6) + 1];
    char escaped_capture_epoch[(PATH_MAX * 6) + 1];
    char payload[(PATH_MAX * 18) + 640];
    int len;
    ssize_t sent;
    unsigned long long timestamp_ns;
    if (!load_capture_context(command_id, sizeof(command_id), epoch, sizeof(epoch))) {
        debug_log("shell_finish_skipped_inactive_capture", NULL);
        return;
    }
    if (!ensure_capture_socket()) {
        debug_log("shell_finish_skipped_no_socket", NULL);
        return;
    }
    if (!json_escape(scope_name, escaped_scope, sizeof(escaped_scope)) ||
        !json_escape(scope_instance_id, escaped_scope_instance, sizeof(escaped_scope_instance)) ||
        !json_escape(command_id, escaped_command_operation, sizeof(escaped_command_operation)) ||
        !json_escape(epoch, escaped_capture_epoch, sizeof(escaped_capture_epoch))) {
        debug_log("shell_finish_skipped_escape_failed", NULL);
        return;
    }
    proc_seq += 1;
    timestamp_ns = (unsigned long long)time(NULL) * 1000000000ULL;
    len = snprintf(
        payload,
        sizeof(payload),
        "{\"binding_name\":\"filesystem\",\"hook_id\":\"filesystem-direct\",\"kind\":\"ld_preload\","
        "\"phase\":\"point\",\"scope\":\"%s\",\"scope_instance_id\":\"%s\",\"pid\":%d,\"proc_seq\":%lu,"
        "\"timestamp_ns\":%llu,\"command_operation_id\":\"%s\",\"capture_epoch\":\"%s\","
        "\"payload\":{\"op\":\"shell_command_finish\",\"seq\":%lu,"
        "\"capture_mechanism\":\"preload\"}}\n",
        escaped_scope,
        escaped_scope_instance,
        getpid(),
        proc_seq,
        timestamp_ns,
        escaped_command_operation,
        escaped_capture_epoch,
        proc_seq
    );
    if (len <= 0 || len >= (int)sizeof(payload)) {
        return;
    }
    sent = send(capture_socket_fd, payload, (size_t)len, MSG_NOSIGNAL);
    if (sent != len) {
        debug_log("shell_finish_send_failed", NULL);
        invalidate_capture_socket();
        return;
    }
    debug_log("shell_command_finish", NULL);
}

static void emit_lifecycle_event(const char *phase, const char *lifecycle) {
    char escaped_scope[(PATH_MAX * 6) + 1];
    char escaped_scope_instance[(PATH_MAX * 6) + 1];
    char escaped_command_operation[(PATH_MAX * 6) + 1];
    char command_operation_json[(PATH_MAX * 6) + 16];
    char escaped_capture_epoch[(PATH_MAX * 6) + 1];
    char capture_epoch_json[(PATH_MAX * 6) + 16];
    char payload[(PATH_MAX * 18) + 640];
    int len;
    ssize_t sent;
    unsigned long long timestamp_ns;
    if (!capture_context_enabled()) {
        debug_log("lifecycle_skipped_inactive_capture", lifecycle);
        return;
    }
    if (!ensure_capture_socket()) {
        debug_log("lifecycle_skipped_no_socket", lifecycle);
        return;
    }
    if (!json_escape(scope_name, escaped_scope, sizeof(escaped_scope)) ||
        !json_escape(scope_instance_id, escaped_scope_instance, sizeof(escaped_scope_instance))) {
        debug_log("lifecycle_skipped_escape_failed", lifecycle);
        return;
    }
    if (command_operation_id[0] != '\0') {
        if (!json_escape(command_operation_id, escaped_command_operation, sizeof(escaped_command_operation))) {
            debug_log("lifecycle_skipped_escape_failed", lifecycle);
            return;
        }
        snprintf(command_operation_json, sizeof(command_operation_json), "\"%s\"", escaped_command_operation);
    } else {
        snprintf(command_operation_json, sizeof(command_operation_json), "null");
    }
    if (capture_epoch[0] != '\0') {
        if (!json_escape(capture_epoch, escaped_capture_epoch, sizeof(escaped_capture_epoch))) {
            debug_log("lifecycle_skipped_escape_failed", lifecycle);
            return;
        }
        snprintf(capture_epoch_json, sizeof(capture_epoch_json), "\"%s\"", escaped_capture_epoch);
    } else {
        snprintf(capture_epoch_json, sizeof(capture_epoch_json), "null");
    }
    timestamp_ns = (unsigned long long)time(NULL) * 1000000000ULL;
    len = snprintf(
        payload,
        sizeof(payload),
        "{\"binding_name\":\"filesystem\",\"hook_id\":\"filesystem-direct\",\"kind\":\"ld_preload\","
        "\"phase\":\"%s\",\"scope\":\"%s\",\"scope_instance_id\":\"%s\",\"pid\":%d,\"proc_seq\":%lu,"
        "\"timestamp_ns\":%llu,\"command_operation_id\":%s,\"capture_epoch\":%s,"
        "\"payload\":{\"capture_lifecycle\":\"%s\",\"last_proc_seq\":%lu}}\n",
        phase,
        escaped_scope,
        escaped_scope_instance,
        getpid(),
        proc_seq,
        timestamp_ns,
        command_operation_json,
        capture_epoch_json,
        lifecycle,
        proc_seq
    );
    if (len <= 0 || len >= (int)sizeof(payload)) {
        return;
    }
    sent = send(capture_socket_fd, payload, (size_t)len, MSG_NOSIGNAL);
    if (sent != len) {
        debug_log("lifecycle_send_failed", lifecycle);
        invalidate_capture_socket();
        return;
    }
    debug_log(lifecycle, NULL);
}

static void clear_fd(int fd) {
    if (fd < 0 || fd >= (int)(sizeof(fd_states) / sizeof(fd_states[0]))) {
        return;
    }
    fd_states[fd].active = 0;
    fd_states[fd].dirty = 0;
    fd_states[fd].write_observed = 0;
    fd_states[fd].path[0] = '\0';
    fd_states[fd].owner_command_operation_id[0] = '\0';
    fd_states[fd].owner_capture_epoch[0] = '\0';
}

static void remember_fd(int fd, const char *path, int flags) {
    char command_id[PATH_MAX];
    char epoch[PATH_MAX];
    if (fd < 0 || fd >= (int)(sizeof(fd_states) / sizeof(fd_states[0])) || path == NULL) {
        return;
    }
    if ((flags & O_WRONLY) == 0 &&
        (flags & O_RDWR) == 0 &&
        (flags & O_CREAT) == 0 &&
        (flags & O_TRUNC) == 0 &&
        (flags & O_APPEND) == 0) {
        return;
    }
    if (!load_capture_context(command_id, sizeof(command_id), epoch, sizeof(epoch))) {
        return;
    }
    fd_states[fd].active = 1;
    fd_states[fd].dirty = 1;
    fd_states[fd].write_observed = 0;
    snprintf(fd_states[fd].path, sizeof(fd_states[fd].path), "%s", path);
    snprintf(fd_states[fd].owner_command_operation_id, sizeof(fd_states[fd].owner_command_operation_id), "%s", command_id);
    snprintf(fd_states[fd].owner_capture_epoch, sizeof(fd_states[fd].owner_capture_epoch), "%s", epoch);
    emit_path_event_with_context("write_open", path, command_id, epoch);
    debug_log("remember_fd", path);
}

static void clone_fd_state(int oldfd, int newfd) {
    if (oldfd < 0 || oldfd >= (int)(sizeof(fd_states) / sizeof(fd_states[0])) ||
        newfd < 0 || newfd >= (int)(sizeof(fd_states) / sizeof(fd_states[0])) ||
        !fd_states[oldfd].active) {
        return;
    }
    fd_states[newfd] = fd_states[oldfd];
}

static void mark_fd_written(int fd) {
    char command_id[PATH_MAX];
    char epoch[PATH_MAX];
    if (fd < 0 || fd >= (int)(sizeof(fd_states) / sizeof(fd_states[0]))) {
        return;
    }
    if (fd_states[fd].active) {
        if (!load_capture_context(command_id, sizeof(command_id), epoch, sizeof(epoch))) {
            return;
        }
        fd_states[fd].dirty = 1;
        fd_states[fd].write_observed = 1;
        snprintf(fd_states[fd].owner_command_operation_id, sizeof(fd_states[fd].owner_command_operation_id), "%s", command_id);
        snprintf(fd_states[fd].owner_capture_epoch, sizeof(fd_states[fd].owner_capture_epoch), "%s", epoch);
        emit_path_event_with_context("write_observed", fd_states[fd].path, command_id, epoch);
        debug_log("mark_dirty", fd_states[fd].path);
    }
}

static void debug_log(const char *message, const char *path) {
    int fd;
    char line[(PATH_MAX * 2) + 128];
    int len;
    if (debug_log_path[0] == '\0' || real_open_fn == NULL || real_write_fn == NULL || real_close_fn == NULL) {
        return;
    }
    fd = real_open_fn(debug_log_path, O_CREAT | O_WRONLY | O_APPEND, 0644);
    if (fd < 0) {
        return;
    }
    len = snprintf(
        line,
        sizeof(line),
        "pid=%d scope=%s msg=%s path=%s\n",
        getpid(),
        scope_name[0] == '\0' ? "-" : scope_name,
        message == NULL ? "-" : message,
        path == NULL ? "-" : path
    );
    if (len > 0 && len < (int)sizeof(line)) {
        real_write_fn(fd, line, (size_t)len);
    }
    real_close_fn(fd);
}

int open(const char *pathname, int flags, ...) {
    mode_t mode = 0;
    int fd;
    char rel_path[PATH_MAX];
    va_list args;
    resolve_symbols();
    if (flags & O_CREAT) {
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
        fd = real_open_fn(pathname, flags, mode);
    } else {
        fd = real_open_fn(pathname, flags);
    }
    if (fd >= 0 && maybe_emit_shell_finish_for_fd(fd)) {
        return fd;
    }
    if (fd >= 0 && resolve_fd_rel_path(fd, rel_path, sizeof(rel_path))) {
        remember_fd(fd, rel_path, flags);
    }
    return fd;
}

int open64(const char *pathname, int flags, ...) {
    mode_t mode = 0;
    int fd;
    char rel_path[PATH_MAX];
    va_list args;
    resolve_symbols();
    if (flags & O_CREAT) {
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
        fd = real_open64_fn(pathname, flags, mode);
    } else {
        fd = real_open64_fn(pathname, flags);
    }
    if (fd >= 0 && maybe_emit_shell_finish_for_fd(fd)) {
        return fd;
    }
    if (fd >= 0 && resolve_fd_rel_path(fd, rel_path, sizeof(rel_path))) {
        remember_fd(fd, rel_path, flags);
    }
    return fd;
}

int openat(int dirfd, const char *pathname, int flags, ...) {
    mode_t mode = 0;
    int fd;
    char rel_path[PATH_MAX];
    va_list args;
    resolve_symbols();
    if (flags & O_CREAT) {
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
        fd = real_openat_fn(dirfd, pathname, flags, mode);
    } else {
        fd = real_openat_fn(dirfd, pathname, flags);
    }
    if (fd >= 0 && maybe_emit_shell_finish_for_fd(fd)) {
        return fd;
    }
    if (fd >= 0 && resolve_fd_rel_path(fd, rel_path, sizeof(rel_path))) {
        remember_fd(fd, rel_path, flags);
    }
    return fd;
}

int openat64(int dirfd, const char *pathname, int flags, ...) {
    mode_t mode = 0;
    int fd;
    char rel_path[PATH_MAX];
    va_list args;
    resolve_symbols();
    if (flags & O_CREAT) {
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
        fd = real_openat64_fn(dirfd, pathname, flags, mode);
    } else {
        fd = real_openat64_fn(dirfd, pathname, flags);
    }
    if (fd >= 0 && maybe_emit_shell_finish_for_fd(fd)) {
        return fd;
    }
    if (fd >= 0 && resolve_fd_rel_path(fd, rel_path, sizeof(rel_path))) {
        remember_fd(fd, rel_path, flags);
    }
    return fd;
}

int creat(const char *pathname, mode_t mode) {
    int fd;
    char rel_path[PATH_MAX];
    resolve_symbols();
    fd = real_creat_fn(pathname, mode);
    if (fd >= 0 && maybe_emit_shell_finish_for_fd(fd)) {
        return fd;
    }
    if (fd >= 0 && resolve_fd_rel_path(fd, rel_path, sizeof(rel_path))) {
        remember_fd(fd, rel_path, O_CREAT | O_WRONLY);
    }
    return fd;
}

ssize_t write(int fd, const void *buf, size_t count) {
    ssize_t result;
    resolve_symbols();
    result = real_write_fn(fd, buf, count);
    if (result >= 0) {
        mark_fd_written(fd);
    }
    return result;
}

ssize_t writev(int fd, const struct iovec *iov, int iovcnt) {
    ssize_t result;
    resolve_symbols();
    result = real_writev_fn(fd, iov, iovcnt);
    if (result >= 0) {
        mark_fd_written(fd);
    }
    return result;
}

ssize_t pwrite(int fd, const void *buf, size_t count, off_t offset) {
    ssize_t result;
    resolve_symbols();
    result = real_pwrite_fn(fd, buf, count, offset);
    if (result >= 0) {
        mark_fd_written(fd);
    }
    return result;
}

int close(int fd) {
    int was_active = 0;
    int was_dirty = 0;
    int write_observed = 0;
    char path[PATH_MAX];
    char owner_command[PATH_MAX];
    char owner_epoch[PATH_MAX];
    int result;
    resolve_symbols();
    if (fd >= 0 && fd < (int)(sizeof(fd_states) / sizeof(fd_states[0])) && fd_states[fd].active) {
        was_active = 1;
        was_dirty = fd_states[fd].dirty;
        write_observed = fd_states[fd].write_observed;
        snprintf(path, sizeof(path), "%s", fd_states[fd].path);
        snprintf(owner_command, sizeof(owner_command), "%s", fd_states[fd].owner_command_operation_id);
        snprintf(owner_epoch, sizeof(owner_epoch), "%s", fd_states[fd].owner_capture_epoch);
    }
    result = real_close_fn(fd);
    if (result == 0 && was_active && was_dirty && write_observed) {
        emit_path_event_with_context("write_close", path, owner_command, owner_epoch);
    }
    clear_fd(fd);
    return result;
}

int dup(int oldfd) {
    int newfd;
    resolve_symbols();
    newfd = real_dup_fn(oldfd);
    if (newfd >= 0) {
        clone_fd_state(oldfd, newfd);
    }
    return newfd;
}

int dup2(int oldfd, int newfd) {
    int result;
    int had_dirty_target = 0;
    int target_write_observed = 0;
    char target_path[PATH_MAX];
    char target_owner_command[PATH_MAX];
    char target_owner_epoch[PATH_MAX];
    resolve_symbols();
    if (oldfd != newfd &&
        newfd >= 0 &&
        newfd < (int)(sizeof(fd_states) / sizeof(fd_states[0])) &&
        fd_states[newfd].active &&
        fd_states[newfd].dirty) {
        had_dirty_target = 1;
        target_write_observed = fd_states[newfd].write_observed;
        snprintf(target_path, sizeof(target_path), "%s", fd_states[newfd].path);
        snprintf(target_owner_command, sizeof(target_owner_command), "%s", fd_states[newfd].owner_command_operation_id);
        snprintf(target_owner_epoch, sizeof(target_owner_epoch), "%s", fd_states[newfd].owner_capture_epoch);
    }
    result = real_dup2_fn(oldfd, newfd);
    if (result >= 0 && had_dirty_target && target_write_observed) {
        emit_path_event_with_context("write_close", target_path, target_owner_command, target_owner_epoch);
    }
    if (result >= 0 && oldfd != newfd) {
        clear_fd(newfd);
        clone_fd_state(oldfd, newfd);
        if (newfd == STDOUT_FILENO || newfd == STDERR_FILENO) {
            mark_fd_written(newfd);
        }
    }
    return result;
}

int dup3(int oldfd, int newfd, int flags) {
    int result;
    int had_dirty_target = 0;
    int target_write_observed = 0;
    char target_path[PATH_MAX];
    char target_owner_command[PATH_MAX];
    char target_owner_epoch[PATH_MAX];
    resolve_symbols();
    if (oldfd != newfd &&
        newfd >= 0 &&
        newfd < (int)(sizeof(fd_states) / sizeof(fd_states[0])) &&
        fd_states[newfd].active &&
        fd_states[newfd].dirty) {
        had_dirty_target = 1;
        target_write_observed = fd_states[newfd].write_observed;
        snprintf(target_path, sizeof(target_path), "%s", fd_states[newfd].path);
        snprintf(target_owner_command, sizeof(target_owner_command), "%s", fd_states[newfd].owner_command_operation_id);
        snprintf(target_owner_epoch, sizeof(target_owner_epoch), "%s", fd_states[newfd].owner_capture_epoch);
    }
    result = real_dup3_fn(oldfd, newfd, flags);
    if (result >= 0 && had_dirty_target && target_write_observed) {
        emit_path_event_with_context("write_close", target_path, target_owner_command, target_owner_epoch);
    }
    if (result >= 0) {
        clear_fd(newfd);
        clone_fd_state(oldfd, newfd);
        if (newfd == STDOUT_FILENO || newfd == STDERR_FILENO) {
            mark_fd_written(newfd);
        }
    }
    return result;
}

static fcntl_arg_kind_t fcntl_arg_kind(int cmd) {
    switch (cmd) {
        case F_DUPFD:
        case F_DUPFD_CLOEXEC:
        case F_SETFD:
        case F_SETFL:
#ifdef F_SETOWN
        case F_SETOWN:
#endif
#ifdef F_SETSIG
        case F_SETSIG:
#endif
#ifdef F_SETLEASE
        case F_SETLEASE:
#endif
#ifdef F_NOTIFY
        case F_NOTIFY:
#endif
#ifdef F_SETPIPE_SZ
        case F_SETPIPE_SZ:
#endif
#ifdef F_ADD_SEALS
        case F_ADD_SEALS:
#endif
            return FCNTL_ARG_INT;
        case F_GETLK:
        case F_SETLK:
        case F_SETLKW:
#if defined(F_GETLK64) && F_GETLK64 != F_GETLK
        case F_GETLK64:
#endif
#if defined(F_SETLK64) && F_SETLK64 != F_SETLK
        case F_SETLK64:
#endif
#if defined(F_SETLKW64) && F_SETLKW64 != F_SETLKW
        case F_SETLKW64:
#endif
#ifdef F_OFD_GETLK
        case F_OFD_GETLK:
#endif
#ifdef F_OFD_SETLK
        case F_OFD_SETLK:
#endif
#ifdef F_OFD_SETLKW
        case F_OFD_SETLKW:
#endif
#ifdef F_GETOWN_EX
        case F_GETOWN_EX:
#endif
#ifdef F_SETOWN_EX
        case F_SETOWN_EX:
#endif
#ifdef F_GET_RW_HINT
        case F_GET_RW_HINT:
#endif
#ifdef F_SET_RW_HINT
        case F_SET_RW_HINT:
#endif
#ifdef F_GET_FILE_RW_HINT
        case F_GET_FILE_RW_HINT:
#endif
#ifdef F_SET_FILE_RW_HINT
        case F_SET_FILE_RW_HINT:
#endif
#ifdef F_GETOWNER_UIDS
        case F_GETOWNER_UIDS:
#endif
            return FCNTL_ARG_PTR;
        default:
            return FCNTL_ARG_NONE;
    }
}

int fcntl(int fd, int cmd, ...) {
    va_list args;
    fcntl_arg_kind_t arg_kind;
    int int_arg = 0;
    void *ptr_arg = NULL;
    int result;
    resolve_symbols();
    arg_kind = fcntl_arg_kind(cmd);
    va_start(args, cmd);
    if (arg_kind == FCNTL_ARG_INT) {
        int_arg = va_arg(args, int);
    } else if (arg_kind == FCNTL_ARG_PTR) {
        ptr_arg = va_arg(args, void *);
    }
    va_end(args);
    if (arg_kind == FCNTL_ARG_INT) {
        result = real_fcntl_fn(fd, cmd, int_arg);
    } else if (arg_kind == FCNTL_ARG_PTR) {
        result = real_fcntl_fn(fd, cmd, ptr_arg);
    } else {
        result = real_fcntl_fn(fd, cmd);
    }
    if (result >= 0 && (cmd == F_DUPFD || cmd == F_DUPFD_CLOEXEC)) {
        clone_fd_state(fd, result);
    }
    return result;
}

int unlink(const char *pathname) {
    char rel_path[PATH_MAX];
    int should_emit;
    int result;
    resolve_symbols();
    should_emit = resolve_unlink_rel_path(AT_FDCWD, pathname, rel_path, sizeof(rel_path));
    result = real_unlink_fn(pathname);
    if (result == 0 && should_emit) {
        emit_path_event("unlink", rel_path);
    }
    return result;
}

int unlinkat(int dirfd, const char *pathname, int flags) {
    char rel_path[PATH_MAX];
    int should_emit;
    int result;
    resolve_symbols();
    should_emit = resolve_unlink_rel_path(dirfd, pathname, rel_path, sizeof(rel_path));
    result = real_unlinkat_fn(dirfd, pathname, flags);
    if (result == 0 && should_emit) {
        emit_path_event("unlink", rel_path);
    }
    return result;
}

int chmod(const char *pathname, mode_t mode) {
    char rel_path[PATH_MAX];
    int should_emit;
    int result;
    resolve_symbols();
    should_emit = resolve_existing_rel_path(AT_FDCWD, pathname, rel_path, sizeof(rel_path));
    result = real_chmod_fn(pathname, mode);
    if (result == 0 && should_emit) {
        emit_path_event("metadata_change", rel_path);
    }
    return result;
}

int fchmod(int fd, mode_t mode) {
    char rel_path[PATH_MAX];
    int should_emit;
    int result;
    resolve_symbols();
    should_emit = resolve_fd_rel_path(fd, rel_path, sizeof(rel_path));
    result = real_fchmod_fn(fd, mode);
    if (result == 0 && should_emit) {
        emit_path_event("metadata_change", rel_path);
    }
    return result;
}

int fchmodat(int dirfd, const char *pathname, mode_t mode, int flags) {
    char rel_path[PATH_MAX];
    int should_emit;
    int result;
    resolve_symbols();
    should_emit = resolve_existing_rel_path(dirfd, pathname, rel_path, sizeof(rel_path));
    result = real_fchmodat_fn(dirfd, pathname, mode, flags);
    if (result == 0 && should_emit) {
        emit_path_event("metadata_change", rel_path);
    }
    return result;
}
