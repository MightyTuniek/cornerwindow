/* CornerWindow Liquid Glass pixel kernel.
 *
 * Plain C99 + POSIX threads, libc + libm only (no X11/GTK headers), loaded from Python
 * with ctypes.
 * Python does everything that isn't per-pixel: capturing the backdrop, the outline
 * geometry, caching. This file only does the work that would take seconds in Python:
 *
 *   cw_edge_field       squared distance to the nearest *visible* edge + an outward
 *                       normal for every pixel within `bezel` of it
 *   cw_prepare_backdrop downsample + blur the backdrop once per change (frost),
 *                       split over a few threads
 *   cw_tint_lo          vibrancy + tint of that small blur (frosted glass's middle)
 *   cw_shade            lensing (refraction through a convex bezel), dispersion,
 *                       saturation, tint, two-sided specular rim, thickness shade,
 *                       press glow, written as premultiplied ARGB32 for cairo; the
 *                       flat middle is the tinted blur upsampled in fixed point
 *
 * Per frame only the rim (within `bezel` of an edge) does real per-pixel maths;
 * everything that depends only on the distance to the edge comes from a table.
 *
 * Pixels are native-endian uint32 0xAARRGGBB, which is what both cairo ARGB32
 * surfaces and 32-bit X11 ZPixmap images are on little-endian machines.
 * Everything is in output-surface pixel coordinates, y down.
 */

#define _POSIX_C_SOURCE 200809L /* pthread_barrier_t */
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    float x0, y0, x1, y1;     /* segment end points */
    float n0x, n0y, n1x, n1y; /* outward vertex normals at the two ends (interpolated) */
} cw_seg;

typedef struct {
    int w, h;                 /* output size in pixels */
    int out_stride;           /* bytes */
    const uint32_t *src;      /* sharp backdrop */
    int src_w, src_h, src_stride;
    float src_ox, src_oy;     /* output (0,0) is at src (src_ox, src_oy) */
    float src_step;           /* src pixels per output pixel (2 = rendering at half resolution) */
    const uint32_t *lo;       /* blurred, downsampled backdrop (may be NULL) */
    int lo_w, lo_h, lo_stride;
    float lo_scale;           /* src pixels per lo pixel */
    float lo_x, lo_y;         /* lo (0,0) is at src (lo_x, lo_y): it may cover only part of src */
    const uint8_t *mask;      /* coverage, A8 */
    int mask_stride;
    const float *dist, *nx, *ny; /* from cw_edge_field, w*h each: squared distance, raw normal */
    float bezel;              /* lens width in px */
    float disp;               /* max refraction offset at the rim, px */
    float dispersion;         /* 0..1 chromatic split of the offset */
    float frost;              /* 0 = sharp, 1 = blurred backdrop */
    float saturation;         /* 1 = unchanged */
    float brightness;         /* added after saturation, -1..1 */
    float tint_r, tint_g, tint_b, tint_a;
    float light_x, light_y;   /* unit vector pointing towards the light */
    float rim;                /* specular strength */
    float rim_ambient;        /* share of the rim that is lit regardless of angle */
    float rim_opposite;       /* strength of the far-side highlight */
    float rim_glow;           /* width of the soft inner glow, px */
    float shade;              /* thickness darkening at the rim */
    float press_x, press_y, press_r, press_a; /* inner illumination at a touch point */
    const uint32_t *lo_tinted; /* cw_tint_lo(lo), same size and stride as lo, or NULL: then the
                                 flat, fully covered middle is just this, upsampled bilinearly
                                 in fixed point (frosted glass's middle is nothing else) */
} cw_params;

static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

/* ── edge field ─────────────────────────────────────────────────────────────── */
/* For every pixel within `bezel` of a visible edge: the *squared* distance to the
   nearest one and the interpolated (not yet normalised) outward normal there.
   Everything else gets a huge distance and undefined normals. cw_shade takes the
   square root and normalises only for the rim pixels it actually shades, so this
   pass never has to walk the whole surface beyond one memset. */
void cw_edge_field(const cw_seg *segs, int n, int w, int h, float bezel,
                   float *dist, float *nx, float *ny) {
    const size_t count = (size_t)w * (size_t)h;
    memset(dist, 0x7f, count * sizeof(float)); /* 0x7f7f7f7f = 3.4e38 */
    const float b2 = bezel * bezel;
    for (int s = 0; s < n; s++) {
        const cw_seg *g = &segs[s];
        const float dx = g->x1 - g->x0, dy = g->y1 - g->y0;
        const float len2 = dx * dx + dy * dy;
        const float inv = len2 > 1e-12f ? 1.0f / len2 : 0.0f;
        int ya = (int)floorf(fminf(g->y0, g->y1) - bezel), yb = (int)ceilf(fmaxf(g->y0, g->y1) + bezel);
        const float gx0 = fminf(g->x0, g->x1) - bezel, gx1 = fmaxf(g->x0, g->x1) + bezel;
        if (ya < 0) ya = 0;
        if (yb > h) yb = h;
        for (int y = ya; y < yb; y++) {
            const float cy = y + 0.5f;
            /* the capsule's slice at this row: |cross| <= bezel*len for the band, plus the
               end caps, i.e. never wider than the bounding box. Narrow it for slanted
               segments so long diagonals don't scan their whole bounding box. */
            float lo = gx0, hi = gx1;
            if (fabsf(dy) > 1e-3f && len2 > 1e-12f) {
                const float half = bezel * sqrtf(len2) / fabsf(dy);
                const float xc = g->x0 + (cy - g->y0) * dx / dy;
                const float band_lo = xc - half, band_hi = xc + half;
                const float cap0 = b2 - (cy - g->y0) * (cy - g->y0), cap1 = b2 - (cy - g->y1) * (cy - g->y1);
                float l = band_lo, r = band_hi;
                if (cap0 > 0) {
                    const float c = sqrtf(cap0);
                    l = fminf(l, g->x0 - c);
                    r = fmaxf(r, g->x0 + c);
                }
                if (cap1 > 0) {
                    const float c = sqrtf(cap1);
                    l = fminf(l, g->x1 - c);
                    r = fmaxf(r, g->x1 + c);
                }
                lo = fmaxf(lo, l);
                hi = fminf(hi, r);
            }
            int xa = (int)floorf(lo - 0.5f), xb = (int)ceilf(hi + 0.5f);
            if (xa < 0) xa = 0;
            if (xb > w) xb = w;
            float *drow = dist + (size_t)y * w;
            float *nxr = nx + (size_t)y * w, *nyr = ny + (size_t)y * w;
            const float ry = cy - g->y0;
            for (int x = xa; x < xb; x++) {
                const float rx = x + 0.5f - g->x0;
                const float t = clampf((rx * dx + ry * dy) * inv, 0.0f, 1.0f);
                const float qx = t * dx - rx, qy = t * dy - ry;
                const float d2 = qx * qx + qy * qy;
                if (d2 < b2 && d2 < drow[x]) {
                    drow[x] = d2;
                    nxr[x] = g->n0x + (g->n1x - g->n0x) * t;
                    nyr[x] = g->n0y + (g->n1y - g->n0y) * t;
                }
            }
        }
    }
}

/* ── backdrop preparation: box downsample + 3-pass box blur ─────────────────── */
/* Rows are RGB float triples, interleaved. Both passes run along memory: the
   horizontal one per row, the vertical one with a whole row of running sums, so
   the compiler can vectorise it and nothing strides down a column. */
static void blur_rows(float *buf, float *line, int lw, int y0, int y1, int r) {
    const float norm = 1.0f / (2 * r + 1);
    for (int y = y0; y < y1; y++) {
        float *row = buf + (size_t)3 * y * lw;
        memcpy(line, row, sizeof(float) * 3 * (size_t)lw);
        float a0 = 0, a1 = 0, a2 = 0;
        for (int k = -r; k <= r; k++) {
            const int i = k < 0 ? 0 : k >= lw ? lw - 1 : k;
            a0 += line[3 * i];
            a1 += line[3 * i + 1];
            a2 += line[3 * i + 2];
        }
        for (int x = 0; x < lw; x++) {
            row[3 * x] = a0 * norm;
            row[3 * x + 1] = a1 * norm;
            row[3 * x + 2] = a2 * norm;
            int add = x + r + 1, sub = x - r;
            add = add >= lw ? lw - 1 : add;
            sub = sub < 0 ? 0 : sub;
            a0 += line[3 * add] - line[3 * sub];
            a1 += line[3 * add + 1] - line[3 * sub + 1];
            a2 += line[3 * add + 2] - line[3 * sub + 2];
        }
    }
}

/* columns j0..j1 of the 3*lw floats per row, with one running sum per column */
static void blur_cols(const float *in, float *out, float *acc, int lw, int lh, int j0, int j1, int r) {
    const size_t n = (size_t)3 * lw;
    const float norm = 1.0f / (2 * r + 1);
    for (int j = j0; j < j1; j++) acc[j] = 0.0f;
    for (int k = -r; k <= r; k++) {
        const float *row = in + n * (k < 0 ? 0 : k >= lh ? lh - 1 : k);
        for (int j = j0; j < j1; j++) acc[j] += row[j];
    }
    for (int y = 0; y < lh; y++) {
        float *o = out + n * y;
        int add = y + r + 1, sub = y - r;
        add = add >= lh ? lh - 1 : add;
        sub = sub < 0 ? 0 : sub;
        const float *pa = in + n * add, *ps = in + n * sub;
        for (int j = j0; j < j1; j++) {
            o[j] = acc[j] * norm;
            acc[j] += pa[j] - ps[j];
        }
    }
}

static void downsample_rows(const uint32_t *src, int sw, int sh, int sstride, int scale, float *buf, uint32_t *sums,
                            int lw, int ya, int yb) {
    const size_t n = (size_t)3 * lw;
    const int full_w = sw / scale < lw ? sw / scale : lw; /* lo columns with a complete block */
    for (int y = ya; y < yb; y++) {
        memset(sums, 0, sizeof(uint32_t) * n);
        const int y0 = y * scale, y1 = (y + 1) * scale < sh ? (y + 1) * scale : sh;
        for (int yy = y0; yy < y1; yy++) {
            const uint32_t *row = (const uint32_t *)((const uint8_t *)src + (size_t)yy * sstride);
            if (scale == 4) {
                for (int x = 0; x < full_w; x++) {
                    const uint32_t *q = row + 4 * x;
                    const uint32_t a = q[0], b = q[1], c = q[2], d = q[3];
                    /* red and blue in one go: they can't carry into each other below 256 sums */
                    const uint32_t rb = (a & 0xff00ffu) + (b & 0xff00ffu) + (c & 0xff00ffu) + (d & 0xff00ffu);
                    sums[3 * x] += rb >> 16;
                    sums[3 * x + 1] += ((a >> 8) & 255) + ((b >> 8) & 255) + ((c >> 8) & 255) + ((d >> 8) & 255);
                    sums[3 * x + 2] += rb & 0xffffu;
                }
            } else {
                for (int x = 0; x < full_w; x++) {
                    const uint32_t *q = row + scale * x;
                    for (int k = 0; k < scale; k++) {
                        sums[3 * x] += (q[k] >> 16) & 255;
                        sums[3 * x + 1] += (q[k] >> 8) & 255;
                        sums[3 * x + 2] += q[k] & 255;
                    }
                }
            }
            for (int x = full_w; x < lw; x++) { /* the ragged right edge */
                for (int xx = x * scale; xx < (x + 1) * scale && xx < sw; xx++) {
                    sums[3 * x] += (row[xx] >> 16) & 255;
                    sums[3 * x + 1] += (row[xx] >> 8) & 255;
                    sums[3 * x + 2] += row[xx] & 255;
                }
            }
        }
        float *o = buf + n * y;
        const int rows = y1 - y0;
        const float inv_full = rows > 0 ? 1.0f / (rows * scale) : 0.0f;
        for (int x = 0; x < full_w; x++) {
            o[3 * x] = sums[3 * x] * inv_full;
            o[3 * x + 1] = sums[3 * x + 1] * inv_full;
            o[3 * x + 2] = sums[3 * x + 2] * inv_full;
        }
        for (int x = full_w; x < lw; x++) {
            int cols = sw - x * scale;
            cols = cols > scale ? scale : cols < 0 ? 0 : cols;
            const float inv = rows * cols > 0 ? 1.0f / (rows * cols) : 0.0f;
            o[3 * x] = sums[3 * x] * inv;
            o[3 * x + 1] = sums[3 * x + 1] * inv;
            o[3 * x + 2] = sums[3 * x + 2] * inv;
        }
    }
}

typedef struct {
    const uint32_t *src;
    int sw, sh, sstride, scale, radius;
    uint32_t *lo;
    int lw, lh, lstride;
    float *buf, *buf2, *acc;
    int threads;              /* final once `go` is set: how many workers really started */
    pthread_barrier_t barrier;
    pthread_mutex_t lock;
    pthread_cond_t start;
    int go;
} prep_job;

typedef struct {
    const prep_job *job;
    int index;
    float *line; /* 3*lw floats of scratch, per thread */
} prep_worker;

static void *prep_run(void *arg) {
    const prep_worker *wk = (const prep_worker *)arg;
    prep_job *j = (prep_job *)wk->job;
    if (wk->index > 0) {
        pthread_mutex_lock(&j->lock);
        while (!j->go) pthread_cond_wait(&j->start, &j->lock);
        pthread_mutex_unlock(&j->lock);
    }
    const int t = wk->index, T = j->threads;
    if (t >= T) return NULL; /* released without work (see cw_prepare_backdrop) */
    const int n = 3 * j->lw;
    const int ya = j->lh * t / T, yb = j->lh * (t + 1) / T;
    /* column chunks for the vertical pass, rounded to 8 floats so threads don't share lines */
    int ja = (n * t / T) & ~7, jb = t + 1 == T ? n : (n * (t + 1) / T) & ~7;
    downsample_rows(j->src, j->sw, j->sh, j->sstride, j->scale, j->buf, (uint32_t *)wk->line, j->lw, ya, yb);
    float *buf = j->buf, *buf2 = j->buf2;
    if (j->radius > 0) {
        for (int pass = 0; pass < 3; pass++) {
            if (T > 1) pthread_barrier_wait(&j->barrier);
            if (j->lw > 1) blur_rows(buf, wk->line, j->lw, ya, yb, j->radius);
            if (j->lh > 1) {
                if (T > 1) pthread_barrier_wait(&j->barrier);
                blur_cols(buf, buf2, j->acc, j->lw, j->lh, ja, jb, j->radius);
                float *tmp = buf;
                buf = buf2;
                buf2 = tmp;
            }
        }
    }
    if (T > 1) pthread_barrier_wait(&j->barrier);
    for (int y = ya; y < yb; y++) {
        uint32_t *row = (uint32_t *)((uint8_t *)j->lo + (size_t)y * j->lstride);
        const float *o = buf + (size_t)n * y;
        for (int x = 0; x < j->lw; x++) {
            const uint32_t r = (uint32_t)clampf(o[3 * x] + 0.5f, 0, 255), g = (uint32_t)clampf(o[3 * x + 1] + 0.5f, 0, 255),
                           b = (uint32_t)clampf(o[3 * x + 2] + 0.5f, 0, 255);
            row[x] = 0xff000000u | (r << 16) | (g << 8) | b;
        }
    }
    return NULL;
}

/* `threads` <= 1 runs on the calling thread; small images always do (spawning costs more) */
int cw_prepare_backdrop(const uint32_t *src, int sw, int sh, int sstride, int scale, int radius,
                        uint32_t *lo, int lw, int lh, int lstride, int threads) {
    if (lw <= 0 || lh <= 0 || scale <= 0) return -1;
    if (threads > 16) threads = 16;
    if (threads > lh / 8) threads = lh / 8;
    if ((size_t)lw * lh < 16384) threads = 1;
    if (threads < 1) threads = 1;
    const size_t n = (size_t)3 * lw;
    prep_job job;
    memset(&job, 0, sizeof job);
    job.src = src, job.sw = sw, job.sh = sh, job.sstride = sstride, job.scale = scale, job.radius = radius;
    job.lo = lo, job.lw = lw, job.lh = lh, job.lstride = lstride;
    job.buf = (float *)malloc(sizeof(float) * n * lh);
    job.buf2 = radius > 0 ? (float *)malloc(sizeof(float) * n * lh) : NULL;
    job.acc = (float *)malloc(sizeof(float) * n);
    float *lines = (float *)malloc(sizeof(float) * n * threads);
    const int ok = job.buf && job.acc && lines && (radius <= 0 || job.buf2);
    if (ok) {
        prep_worker workers[16];
        pthread_t ids[16];
        for (int t = 0; t < threads; t++) workers[t] = (prep_worker){&job, t, lines + n * t};
        int started = 1;
        if (threads > 1) {
            pthread_mutex_init(&job.lock, NULL);
            pthread_cond_init(&job.start, NULL);
            /* workers wait for `go`, so the split can follow however many really started */
            while (started < threads && pthread_create(&ids[started], NULL, prep_run, &workers[started]) == 0)
                started++;
            if (started > 1 && pthread_barrier_init(&job.barrier, NULL, (unsigned)started) != 0) {
                /* no barrier: the started workers still have to be released and joined */
                job.threads = 0;
            } else {
                job.threads = started;
            }
        } else {
            job.threads = 1;
        }
        if (threads > 1) {
            pthread_mutex_lock(&job.lock);
            job.go = 1;
            pthread_cond_broadcast(&job.start);
            pthread_mutex_unlock(&job.lock);
        }
        if (job.threads == 0) { /* workers see 0 threads and return at once (see prep_run) */
            for (int t = 1; t < started; t++) pthread_join(ids[t], NULL);
            job.threads = 1;
            prep_run(&workers[0]);
        } else {
            prep_run(&workers[0]);
            for (int t = 1; t < started; t++) pthread_join(ids[t], NULL);
            if (started > 1) pthread_barrier_destroy(&job.barrier);
        }
        if (threads > 1) {
            pthread_mutex_destroy(&job.lock);
            pthread_cond_destroy(&job.start);
        }
    }
    free(job.buf);
    free(job.buf2);
    free(job.acc);
    free(lines);
    return ok ? 0 : -1;
}

/* ── sampling ───────────────────────────────────────────────────────────────── */
static inline void sample(const uint32_t *img, int w, int h, int stride, float x, float y, float *rgb) {
    /* bilinear, (x, y) in pixel-centre coordinates, clamped to the image */
    x = clampf(x - 0.5f, 0.0f, (float)(w - 1));
    y = clampf(y - 0.5f, 0.0f, (float)(h - 1));
    int x0 = (int)x, y0 = (int)y;
    int x1 = x0 + 1 < w ? x0 + 1 : x0, y1 = y0 + 1 < h ? y0 + 1 : y0;
    float fx = x - x0, fy = y - y0;
    const uint32_t *r0 = (const uint32_t *)((const uint8_t *)img + (size_t)y0 * stride);
    const uint32_t *r1 = (const uint32_t *)((const uint8_t *)img + (size_t)y1 * stride);
    uint32_t a = r0[x0], b = r0[x1], c = r1[x0], d = r1[x1];
    float wa = (1 - fx) * (1 - fy), wb = fx * (1 - fy), wc = (1 - fx) * fy, wd = fx * fy;
    rgb[0] = (((a >> 16) & 255) * wa + ((b >> 16) & 255) * wb + ((c >> 16) & 255) * wc + ((d >> 16) & 255) * wd) * (1.0f / 255);
    rgb[1] = (((a >> 8) & 255) * wa + ((b >> 8) & 255) * wb + ((c >> 8) & 255) * wc + ((d >> 8) & 255) * wd) * (1.0f / 255);
    rgb[2] = ((a & 255) * wa + (b & 255) * wb + (c & 255) * wc + (d & 255) * wd) * (1.0f / 255);
}

/* one channel only (shift 16 = red, 8 = green, 0 = blue): the dispersion path needs
   a different offset per channel, so sampling all three each time wastes two thirds */
static inline float sample_ch(const uint32_t *img, int w, int h, int stride, float x, float y, int shift) {
    x = clampf(x - 0.5f, 0.0f, (float)(w - 1));
    y = clampf(y - 0.5f, 0.0f, (float)(h - 1));
    int x0 = (int)x, y0 = (int)y;
    int x1 = x0 + 1 < w ? x0 + 1 : x0, y1 = y0 + 1 < h ? y0 + 1 : y0;
    float fx = x - x0, fy = y - y0;
    const uint32_t *r0 = (const uint32_t *)((const uint8_t *)img + (size_t)y0 * stride);
    const uint32_t *r1 = (const uint32_t *)((const uint8_t *)img + (size_t)y1 * stride);
    const float a = (r0[x0] >> shift) & 255, b = (r0[x1] >> shift) & 255;
    const float c = (r1[x0] >> shift) & 255, d = (r1[x1] >> shift) & 255;
    const float top = a + (b - a) * fx, bot = c + (d - c) * fx;
    return (top + (bot - top) * fy) * (1.0f / 255);
}

static inline float backdrop_ch(const cw_params *p, float x, float y, int shift) {
    float sx = x * p->src_step + p->src_ox, sy = y * p->src_step + p->src_oy;
    const float lx = (sx - p->lo_x) / p->lo_scale, ly = (sy - p->lo_y) / p->lo_scale;
    if (p->lo && p->frost >= 0.999f) return sample_ch(p->lo, p->lo_w, p->lo_h, p->lo_stride, lx, ly, shift);
    float v = sample_ch(p->src, p->src_w, p->src_h, p->src_stride, sx, sy, shift);
    if (p->lo && p->frost > 0.001f)
        v += (sample_ch(p->lo, p->lo_w, p->lo_h, p->lo_stride, lx, ly, shift) - v) * p->frost;
    return v;
}

static inline void backdrop(const cw_params *p, float x, float y, float *rgb) {
    /* (x, y) in output coordinates */
    float sx = x * p->src_step + p->src_ox, sy = y * p->src_step + p->src_oy;
    const float lx = (sx - p->lo_x) / p->lo_scale, ly = (sy - p->lo_y) / p->lo_scale;
    if (p->lo && p->frost >= 0.999f) {
        sample(p->lo, p->lo_w, p->lo_h, p->lo_stride, lx, ly, rgb);
        return;
    }
    sample(p->src, p->src_w, p->src_h, p->src_stride, sx, sy, rgb);
    if (p->lo && p->frost > 0.001f) {
        float b[3];
        sample(p->lo, p->lo_w, p->lo_h, p->lo_stride, lx, ly, b);
        for (int c = 0; c < 3; c++) rgb[c] += (b[c] - rgb[c]) * p->frost;
    }
}

static inline float screen(float c, float k) { return 1.0f - (1.0f - c) * (1.0f - k); }

static inline void vibrancy(const cw_params *p, float *rgb) {
    /* saturate around the luminance, lift or lower it, then tint */
    const float l = 0.2126f * rgb[0] + 0.7152f * rgb[1] + 0.0722f * rgb[2];
    const float tint[3] = {p->tint_r, p->tint_g, p->tint_b};
    for (int c = 0; c < 3; c++) {
        float v = clampf(l + (rgb[c] - l) * p->saturation + p->brightness, 0.0f, 1.0f);
        rgb[c] = v + (tint[c] - v) * p->tint_a;
    }
}

/* The flat middle of frosted glass is just the blurred backdrop with vibrancy and
   tint: do that once at low resolution; cw_shade upsamples it (p->lo_tinted).
   `dst` has the same size and stride as p->lo. */
void cw_tint_lo_rows(const cw_params *p, uint32_t *dst, int y_start, int y_end, int y_step) {
    if (y_step < 1) y_step = 1;
    if (y_end > p->lo_h) y_end = p->lo_h;
    for (int y = y_start; y < y_end; y += y_step) {
        const uint32_t *row = (const uint32_t *)((const uint8_t *)p->lo + (size_t)y * p->lo_stride);
        uint32_t *out = (uint32_t *)((uint8_t *)dst + (size_t)y * p->lo_stride);
        for (int x = 0; x < p->lo_w; x++) {
            uint32_t v = row[x];
            float rgb[3] = {((v >> 16) & 255) / 255.0f, ((v >> 8) & 255) / 255.0f, (v & 255) / 255.0f};
            vibrancy(p, rgb);
            out[x] = 0xff000000u | ((uint32_t)(rgb[0] * 255.0f + 0.5f) << 16)
                     | ((uint32_t)(rgb[1] * 255.0f + 0.5f) << 8) | (uint32_t)(rgb[2] * 255.0f + 0.5f);
        }
    }
}

void cw_tint_lo(const cw_params *p, uint32_t *dst) { cw_tint_lo_rows(p, dst, 0, p->lo_h, 1); }

/* ── shading ────────────────────────────────────────────────────────────────── */
/* Everything that depends only on the distance to the edge, tabulated per call at
   LUT_RES steps per pixel and interpolated: no expf() per pixel. */
#define LUT_RES 8.0f

typedef struct {
    float *off, *spec, *shade; /* refraction offset, rim line + glow, thickness shade */
    int n;
} cw_lut;

static int lut_build(const cw_params *p, cw_lut *t) {
    const float inv_bezel = p->bezel > 0.01f ? 1.0f / p->bezel : 0.0f;
    const float glow_inv = p->rim_glow > 0.01f ? 1.0f / p->rim_glow : 0.0f;
    t->n = (int)ceilf(p->bezel * LUT_RES) + 2;
    t->off = (float *)malloc(sizeof(float) * 3 * (size_t)t->n);
    if (!t->off) return -1;
    t->spec = t->off + t->n;
    t->shade = t->spec + t->n;
    for (int i = 0; i < t->n; i++) {
        const float d = i / LUT_RES;
        /* convex bezel: 0 offset where the flat top begins, `disp` at the rim,
           with zero slope at both ends so there is no visible seam */
        const float u = clampf(1.0f - d * inv_bezel, 0.0f, 1.0f);
        t->off[i] = p->disp * u * u * (3.0f - 2.0f * u);
        t->spec[i] = expf(-(d - 0.5f) * (d - 0.5f) * 1.6f) + expf(-d * glow_inv) * 0.35f;
        t->shade[i] = p->shade * expf(-d * inv_bezel * 3.0f);
    }
    return 0;
}

static inline uint32_t pack(const float *rgb, uint32_t m) {
    const float a = m * (1.0f / 255.0f) * 255.0f;
    const uint32_t r = (uint32_t)(clampf(rgb[0], 0, 1) * a + 0.5f);
    const uint32_t g = (uint32_t)(clampf(rgb[1], 0, 1) * a + 0.5f);
    const uint32_t b = (uint32_t)(clampf(rgb[2], 0, 1) * a + 0.5f);
    return (m << 24) | (r << 16) | (g << 8) | b;
}

/* a + (b - a) * f / 256 for the three colour bytes at once (red and blue share a multiply) */
static inline uint32_t lerp_px(uint32_t a, uint32_t b, uint32_t f) {
    const uint32_t g = 256 - f;
    const uint32_t rb = (((a & 0xff00ffu) * g + (b & 0xff00ffu) * f) >> 8) & 0xff00ffu;
    const uint32_t gg = (((a & 0x00ff00u) * g + (b & 0x00ff00u) * f) >> 8) & 0x00ff00u;
    return rb | gg;
}

typedef struct {
    uint32_t *vrow; /* lo_w + 1: the two lo rows around this output row, blended vertically */
    uint32_t *tmp;  /* w: one run of pixels */
    const uint32_t *vimg;
    int vy;         /* which output row vrow holds, or -1 */
} cw_scratch;

/* `img` (lo or lo_tinted) scaled up to output pixels xa..xb of row y, bilinear with
   edges clamped like cairo's EXTEND_PAD: blend the two rows once per output row,
   then walk across in 16.16 fixed point. */
static void upsample_run(const cw_params *p, cw_scratch *s, const uint32_t *img, int y, int xa, int xb,
                         uint32_t *dst) {
    const float inv_scale = 1.0f / p->lo_scale;
    if (s->vy != y || s->vimg != img) {
        float ly = ((y + 0.5f) * p->src_step + p->src_oy - p->lo_y) * inv_scale - 0.5f;
        ly = clampf(ly, 0.0f, (float)(p->lo_h - 1));
        const int y0 = (int)ly, y1 = y0 + 1 < p->lo_h ? y0 + 1 : y0;
        const uint32_t fy = (uint32_t)((ly - y0) * 256.0f + 0.5f);
        const uint32_t *r0 = (const uint32_t *)((const uint8_t *)img + (size_t)y0 * p->lo_stride);
        const uint32_t *r1 = (const uint32_t *)((const uint8_t *)img + (size_t)y1 * p->lo_stride);
        for (int i = 0; i < p->lo_w; i++) s->vrow[i] = lerp_px(r0[i], r1[i], fy);
        s->vrow[p->lo_w] = s->vrow[p->lo_w - 1];
        s->vy = y;
        s->vimg = img;
    }
    const uint32_t *v = s->vrow;
    const float lx0 = ((xa + 0.5f) * p->src_step + p->src_ox - p->lo_x) * inv_scale - 0.5f;
    int32_t fx = (int32_t)lrintf(lx0 * 65536.0f);
    const int32_t step = (int32_t)lrintf(p->src_step * inv_scale * 65536.0f);
    const int32_t fmax = (p->lo_w - 1) << 16;
    for (int x = xa; x < xb; x++, fx += step) {
        const int32_t c = fx < 0 ? 0 : fx > fmax ? fmax : fx;
        const int i = c >> 16;
        dst[x - xa] = 0xff000000u | lerp_px(v[i], v[i + 1], (uint32_t)(c >> 8) & 255);
    }
}

/* vibrancy (see above) over a run of opaque pixels, branch free so it vectorises */
static void vibrancy_run(const cw_params *p, const uint32_t *in, uint32_t *out, int n) {
    const float sat = p->saturation, bright = p->brightness, ta = p->tint_a;
    const float tr = p->tint_r * ta * 255.0f, tg = p->tint_g * ta * 255.0f, tb = p->tint_b * ta * 255.0f;
    const float keep = (1.0f - ta) * 255.0f;
    for (int i = 0; i < n; i++) {
        const uint32_t v = in[i];
        const float r = ((v >> 16) & 255) * (1.0f / 255), g = ((v >> 8) & 255) * (1.0f / 255),
                    b = (v & 255) * (1.0f / 255);
        const float l = 0.2126f * r + 0.7152f * g + 0.0722f * b;
        const float vr = fminf(fmaxf(l + (r - l) * sat + bright, 0.0f), 1.0f);
        const float vg = fminf(fmaxf(l + (g - l) * sat + bright, 0.0f), 1.0f);
        const float vb = fminf(fmaxf(l + (b - l) * sat + bright, 0.0f), 1.0f);
        const uint32_t R = (uint32_t)(vr * keep + tr + 0.5f), G = (uint32_t)(vg * keep + tg + 0.5f),
                       B = (uint32_t)(vb * keep + tb + 0.5f);
        out[i] = 0xff000000u | (R << 16) | (G << 8) | B;
    }
}

/* the press glow, screened onto opaque pixels xa..xb (clipped to where it reaches) */
static void press_run(const cw_params *p, uint32_t *orow, int xa, int xb, int px0, int px1,
                      float pdy2, float press_inv) {
    const int a = xa > px0 ? xa : px0, b = xb < px1 ? xb : px1;
    for (int i = a; i < b; i++) {
        const float ddx = i + 0.5f - p->press_x;
        const float k = p->press_a * expf(-(ddx * ddx + pdy2) * press_inv);
        const uint32_t v = orow[i];
        float rgb[3] = {((v >> 16) & 255) * (1.0f / 255), ((v >> 8) & 255) * (1.0f / 255), (v & 255) * (1.0f / 255)};
        for (int c = 0; c < 3; c++) rgb[c] = screen(rgb[c], k);
        orow[i] = pack(rgb, 255);
    }
}

/* The flat, fully covered middle of row y, xa..xb (no rim, no lensing). */
static void shade_interior(const cw_params *p, cw_scratch *s, uint32_t *orow, int y, int xa, int xb) {
    const int n = xb - xa;
    const int frosted = p->lo && p->frost >= 0.999f, mixed = p->lo && p->frost > 0.001f && !frosted;
    if (frosted && p->lo_tinted) { /* frosted: the tinted blur, scaled up */
        upsample_run(p, s, p->lo_tinted, y, xa, xb, orow + xa);
        return;
    }
    if (frosted) {
        upsample_run(p, s, p->lo, y, xa, xb, s->tmp);
        vibrancy_run(p, s->tmp, orow + xa, n);
        return;
    }
    /* clear or lightly frosted: at full quality the backdrop maps 1:1 onto output pixels */
    const int sy = y + (int)p->src_oy, sx = xa + (int)p->src_ox;
    if (p->src_step == 1.0f && p->src_ox == floorf(p->src_ox) && p->src_oy == floorf(p->src_oy) &&
        sy >= 0 && sy < p->src_h && sx >= 0 && sx + n <= p->src_w) {
        const uint32_t *in = (const uint32_t *)((const uint8_t *)p->src + (size_t)sy * p->src_stride) + sx;
        if (mixed) {
            upsample_run(p, s, p->lo, y, xa, xb, s->tmp);
            const uint32_t f = (uint32_t)(p->frost * 256.0f + 0.5f);
            for (int i = 0; i < n; i++) s->tmp[i] = lerp_px(in[i], s->tmp[i], f);
            in = s->tmp;
        }
        vibrancy_run(p, in, orow + xa, n);
        return;
    }
    const float cy = y + 0.5f;
    for (int x = xa; x < xb; x++) {
        float rgb[3];
        backdrop(p, x + 0.5f, cy, rgb);
        vibrancy(p, rgb);
        orow[x] = pack(rgb, 255);
    }
}

/* rows y_start, y_start + y_step, ... below y_end (interleaved rows balance threads) */
void cw_shade_rows(const cw_params *params, uint8_t *out, int y_start, int y_end, int y_step) {
    /* a private copy: the compiler can then keep fields in registers instead of
       reloading them after every pixel store (which could otherwise alias them) */
    const cw_params local = *params;
    const cw_params *p = &local;
    cw_lut lut;
    if (lut_build(p, &lut) != 0) return;
    cw_scratch scratch = {NULL, NULL, NULL, -1};
    scratch.vrow = (uint32_t *)malloc(sizeof(uint32_t) * ((size_t)(p->lo ? p->lo_w : 0) + 1 + (size_t)p->w));
    if (!scratch.vrow) {
        free(lut.off);
        return;
    }
    scratch.tmp = scratch.vrow + (p->lo ? p->lo_w : 0) + 1;
    const float b2 = p->bezel * p->bezel;
    const float press_inv = p->press_r > 0.5f ? 1.0f / (p->press_r * p->press_r) : 0.0f;
    const int pressing = press_inv > 0.0f && p->press_a > 0.001f;
    /* beyond this radius the press glow rounds to nothing in 8 bits */
    const float press_reach2 = pressing && p->press_a * 510.0f > 1.0f
                                   ? p->press_r * p->press_r * logf(p->press_a * 510.0f) : 0.0f;
    if (y_step < 1) y_step = 1;
    for (int y = y_start; y < y_end; y += y_step) {
        uint32_t *orow = (uint32_t *)(out + (size_t)y * p->out_stride);
        const uint8_t *mrow = p->mask + (size_t)y * p->mask_stride;
        const float *drow = p->dist + (size_t)y * p->w;
        const float *nxr = p->nx + (size_t)y * p->w, *nyr = p->ny + (size_t)y * p->w;
        const float cy = y + 0.5f;
        const float pdy = cy - p->press_y, pdy2 = pdy * pdy;
        const int row_pressed = press_reach2 > 0.0f && pdy2 < press_reach2;
        const float press_k0 = row_pressed ? p->press_a : 0.0f; /* 0: the glow doesn't reach this row */
        /* press glow columns on this row */
        int px0 = 0, px1 = 0;
        if (row_pressed) {
            const float half = sqrtf(press_reach2 - pdy2);
            px0 = (int)floorf(p->press_x - half);
            px1 = (int)ceilf(p->press_x + half);
        }
        int x = 0;
        while (x < p->w) {
            const uint32_t m = mrow[x];
            if (!m) {
                int e = x + 1;
                while (e < p->w && !mrow[e]) e++;
                memset(orow + x, 0, sizeof(uint32_t) * (size_t)(e - x));
                x = e;
                continue;
            }
            if (m == 255 && drow[x] >= b2) { /* a run of flat, fully covered middle */
                int e = x + 1;
                while (e < p->w && mrow[e] == 255 && drow[e] >= b2) e++;
                shade_interior(p, &scratch, orow, y, x, e);
                if (row_pressed) press_run(p, orow, x, e, px0, px1, pdy2, press_inv);
                x = e;
                continue;
            }
            const float cx = x + 0.5f;
            const float d2 = drow[x];
            float rgb[3];
            float spec = 0.0f, shade = 0.0f;
            if (d2 < b2) {
                const float d = sqrtf(d2);
                float nxv = nxr[x], nyv = nyr[x];
                const float l2 = nxv * nxv + nyv * nyv;
                if (l2 > 1e-12f) {
                    const float il = 1.0f / sqrtf(l2);
                    nxv *= il;
                    nyv *= il;
                }
                const float f = d * LUT_RES;
                int li = (int)f;
                if (li > lut.n - 2) li = lut.n - 2;
                const float lt = f - li;
                const float off = lut.off[li] + (lut.off[li + 1] - lut.off[li]) * lt;
                const float sb = lut.spec[li] + (lut.spec[li + 1] - lut.spec[li]) * lt;
                const float sh = lut.shade[li] + (lut.shade[li + 1] - lut.shade[li]) * lt;
                const float k = 0.18f * p->dispersion;
                if (off * k > 0.15f) { /* a smaller colour split is invisible: one sample will do */
                    rgb[0] = backdrop_ch(p, cx - nxv * off * (1 + k), cy - nyv * off * (1 + k), 16);
                    rgb[1] = backdrop_ch(p, cx - nxv * off, cy - nyv * off, 8);
                    rgb[2] = backdrop_ch(p, cx - nxv * off * (1 - k), cy - nyv * off * (1 - k), 0);
                } else {
                    backdrop(p, cx - nxv * off, cy - nyv * off, rgb);
                }
                /* two-sided specular: brightest where the edge faces the light,
                   a weaker reflection on the far side */
                const float facing = nxv * p->light_x + nyv * p->light_y;
                const float near_side = facing > 0 ? facing * sqrtf(facing) : 0.0f;
                const float far_side = facing < 0 ? -facing * sqrtf(-facing) : 0.0f;
                const float lit = p->rim_ambient + (1.0f - p->rim_ambient) * (near_side + p->rim_opposite * far_side);
                spec = p->rim * lit * sb;
                shade = sh * (0.4f + 0.6f * far_side);
            } else {
                backdrop(p, cx, cy, rgb);
            }
            vibrancy(p, rgb);
            const float sp = clampf(spec, 0.0f, 1.0f);
            for (int c = 0; c < 3; c++) rgb[c] = screen(rgb[c] * (1.0f - shade), sp);
            if (press_k0 > 0.0f) {
                const float ddx = cx - p->press_x;
                const float k = press_k0 * expf(-(ddx * ddx + pdy2) * press_inv);
                for (int c = 0; c < 3; c++) rgb[c] = screen(rgb[c], k);
            }
            orow[x] = pack(rgb, m);
            x++;
        }
    }
    free(lut.off);
    free(scratch.vrow);
}

void cw_shade(const cw_params *p, uint8_t *out) { cw_shade_rows(p, out, 0, p->h, 1); }

/* mean luminance (0..1) of an image region, for the adaptive appearance */
double cw_luma(const uint32_t *img, int w, int h, int stride) {
    double acc = 0.0;
    long n = 0;
    for (int y = 0; y < h; y++) {
        const uint32_t *row = (const uint32_t *)((const uint8_t *)img + (size_t)y * stride);
        for (int x = 0; x < w; x++) {
            uint32_t p = row[x];
            acc += 0.2126 * ((p >> 16) & 255) + 0.7152 * ((p >> 8) & 255) + 0.0722 * (p & 255);
            n++;
        }
    }
    return n ? acc / n / 255.0 : 0.0;
}

int cw_abi_version(void) { return 6; }
