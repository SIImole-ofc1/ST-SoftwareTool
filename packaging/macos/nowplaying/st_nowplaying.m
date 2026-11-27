// What macOS shows as "Now Playing" (any app: Apple Music, Spotify, YouTube or SoundCloud in a
// browser …) and its play / pause / next / previous commands, for the Music & Audio widget.
//
// Since macOS 15.4 only Apple-signed programs may read Now Playing, so this library is loaded
// into the system's /usr/bin/perl (see core/media.py) and run from there:
//
//   ST_NP_CMD=get ST_NP_ART_DIR=<dir>  →  one line of JSON on stdout
//   ST_NP_CMD=playpause|play|pause|next|previous  →  {"ok":true}
//
// Built by packaging/macos/nowplaying/build.sh (universal arm64 + x86_64).
#import <AppKit/AppKit.h>
#include <dlfcn.h>
#include <objc/message.h>
#include <stdio.h>

typedef void (*GetInfoFn)(dispatch_queue_t, void (^)(CFDictionaryRef));
typedef void (*GetPidFn)(dispatch_queue_t, void (^)(int));
typedef void (*GetIsPlayingFn)(dispatch_queue_t, void (^)(Boolean));
typedef Boolean (*SendFn)(int, CFDictionaryRef);

static void emit(id obj) {
    NSData *json = [NSJSONSerialization dataWithJSONObject:obj options:0 error:nil];
    if (!json) json = [@"{\"error\":\"json\"}" dataUsingEncoding:NSUTF8StringEncoding];
    fwrite(json.bytes, 1, json.length, stdout);
    fputc('\n', stdout);
    fflush(stdout);
}

static id str(id v) { return [v isKindOfClass:[NSString class]] ? v : @""; }
static id num(id v) { return [v isKindOfClass:[NSNumber class]] ? v : [NSNull null]; }

// Newer macOS leaves the picture out of the info and hands it over on request.
static NSData *request_artwork(void) {
    Class cls = NSClassFromString(@"MRNowPlayingRequest");
    SEL sel = NSSelectorFromString(@"requestNowPlayingItemArtworkWithCompletion:");
    if (!cls || ![cls instancesRespondToSelector:sel]) return nil;
    id req = [[cls alloc] init];
    __block NSData *data = nil;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    void (^completion)(id, NSError *) = ^(id artwork, NSError *err) {
        (void)err;
        if ([artwork respondsToSelector:@selector(imageData)]) {
            id d = [artwork valueForKey:@"imageData"];
            if ([d isKindOfClass:[NSData class]]) data = d;
        }
        dispatch_semaphore_signal(done);
    };
    ((void (*)(id, SEL, id))objc_msgSend)(req, sel, completion);
    dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
    return data;
}

static void get_now_playing(void *mr) {
    GetInfoFn getInfo = (GetInfoFn)dlsym(mr, "MRMediaRemoteGetNowPlayingInfo");
    GetPidFn getPid = (GetPidFn)dlsym(mr, "MRMediaRemoteGetNowPlayingApplicationPID");
    GetIsPlayingFn getPlaying = (GetIsPlayingFn)dlsym(mr, "MRMediaRemoteGetNowPlayingApplicationIsPlaying");
    if (!getInfo) { emit(@{@"error": @"no MediaRemote"}); return; }

    dispatch_queue_t q = dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0);
    dispatch_group_t group = dispatch_group_create();
    __block NSDictionary *info = nil;
    __block int pid = 0;
    __block int playing = -1;

    dispatch_group_enter(group);
    getInfo(q, ^(CFDictionaryRef d) { info = [(__bridge NSDictionary *)d copy]; dispatch_group_leave(group); });
    if (getPid) {
        dispatch_group_enter(group);
        getPid(q, ^(int p) { pid = p; dispatch_group_leave(group); });
    }
    if (getPlaying) {
        dispatch_group_enter(group);
        getPlaying(q, ^(Boolean b) { playing = b ? 1 : 0; dispatch_group_leave(group); });
    }
    if (dispatch_group_wait(group, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC)) != 0) {
        emit(@{@"error": @"timeout"});
        return;
    }
    if (!info.count) { emit(@{@"state": @"none"}); return; }

    NSMutableDictionary *out = [NSMutableDictionary dictionary];
    out[@"title"] = str(info[@"kMRMediaRemoteNowPlayingInfoTitle"]);
    out[@"artist"] = str(info[@"kMRMediaRemoteNowPlayingInfoArtist"]);
    out[@"album"] = str(info[@"kMRMediaRemoteNowPlayingInfoAlbum"]);
    out[@"duration"] = num(info[@"kMRMediaRemoteNowPlayingInfoDuration"]);
    out[@"elapsed"] = num(info[@"kMRMediaRemoteNowPlayingInfoElapsedTime"]);
    out[@"rate"] = num(info[@"kMRMediaRemoteNowPlayingInfoPlaybackRate"]);
    id ts = info[@"kMRMediaRemoteNowPlayingInfoTimestamp"];
    out[@"timestamp"] = [ts isKindOfClass:[NSDate class]] ? @([ts timeIntervalSince1970]) : [NSNull null];
    out[@"playing"] = playing < 0 ? [NSNull null] : @(playing == 1);
    out[@"pid"] = @(pid);

    // the app that plays it
    NSRunningApplication *app = pid > 0 ? [NSRunningApplication runningApplicationWithProcessIdentifier:pid] : nil;
    out[@"app"] = app.localizedName ?: @"";
    out[@"bundle"] = app.bundleIdentifier ?: @"";

    // the cover, written once per track into ST_NP_ART_DIR
    const char *dir = getenv("ST_NP_ART_DIR");
    NSData *art = info[@"kMRMediaRemoteNowPlayingInfoArtworkData"];
    if (![art isKindOfClass:[NSData class]]) art = nil;
    NSString *ident = str(info[@"kMRMediaRemoteNowPlayingInfoArtworkIdentifier"]);
    if (!ident.length && art.length) ident = [NSString stringWithFormat:@"%lu", (unsigned long)art.hash];
    if (dir && ident.length) {
        NSString *safe = [[ident componentsSeparatedByCharactersInSet:
            [[NSCharacterSet alphanumericCharacterSet] invertedSet]] componentsJoinedByString:@"_"];
        NSString *path = [[NSString stringWithUTF8String:dir] stringByAppendingPathComponent:
                          [NSString stringWithFormat:@"np-%@.img", safe]];
        if (![[NSFileManager defaultManager] fileExistsAtPath:path]) {
            if (!art.length) art = request_artwork();
            if (art.length) [art writeToFile:path atomically:YES];
        }
        if ([[NSFileManager defaultManager] fileExistsAtPath:path]) out[@"artwork"] = path;
    }
    emit(out);
}

static int command_code(const char *cmd) {
    if (!strcmp(cmd, "play")) return 0;
    if (!strcmp(cmd, "pause")) return 1;
    if (!strcmp(cmd, "playpause")) return 2;
    if (!strcmp(cmd, "next")) return 4;
    if (!strcmp(cmd, "previous")) return 5;
    return -1;
}

// Called from perl as an XSUB: (PerlInterpreter *, CV *) — both unused.
__attribute__((visibility("default")))
void st_nowplaying(void *interp, void *cv) {
    (void)interp; (void)cv;
    @autoreleasepool {
        void *mr = dlopen("/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote", RTLD_NOW);
        const char *cmd = getenv("ST_NP_CMD");
        if (!mr) { emit(@{@"error": @"no MediaRemote"}); return; }
        if (!cmd || !strcmp(cmd, "get")) { get_now_playing(mr); return; }
        int code = command_code(cmd);
        SendFn send = (SendFn)dlsym(mr, "MRMediaRemoteSendCommand");
        if (code < 0 || !send) { emit(@{@"ok": @NO}); return; }
        BOOL ok = send(code, NULL) ? YES : NO;
        // the command goes out asynchronously: give it time before this process ends
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.4]];
        emit(@{@"ok": @(ok)});
    }
}
