/* CornerWindow Liquid Glass pixel kernel.
 *
 * Plain C99, libc + libm only (no X11/GTK headers), loaded from Python with ctypes.
 * Python does everything that isn't per-pixel: capturing the backdrop, the outline
 * geometry, caching. This file only does the work that would take seconds in Python:
 *
 *   cw_edge_field       distance to the nearest *visible* edge + a smooth outward
 *                       normal for every pixel within `bezel` of it
 *   cw_prepare_backdrop downsample + blur the backdrop once per change (frost)
 *   cw_shade            lensing (refraction through a convex bezel), dispersion,
 *                       saturation, tint, two-sided specular rim, thickness shade,
 *                       press glow, written as premultiplied ARGB32 for cairo
 *
 * Pixels are native-endian uint32 0xAARRGGBB, which is what both cairo ARGB32
 * surfaces and 32-bit X11 ZPixmap images are on little-endian machines.
 * Everything is in output-surface pixel coordinates, y down.
 */

#include <math.h>
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
    const float *dist, *nx, *ny; /* from cw_edge_field, w*h each */
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
    int skip_interior;        /* 1: the flat, fully covered middle was already painted by the
                                 caller (cw_tint_lo upsampled by cairo); leave it untouched */
} cw_params;

#define BIG 1e30f

static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

/* ── edge field ─────────────────────────────────────────────────────────────── */
void cw_edge_field(const cw_seg *segs, int n, int w, int h, float bezel,
                   float *dist, float *nx, float *ny) {
    const size_t count = (size_t)w * (size_t)h;
    for (size_t i = 0; i < count; i++) {
        dist[i] = BIG;
        nx[i] = 0.0f;
        ny[i] = 0.0f;
    }
    const float b2 = bezel * bezel;
    for (int s = 0; s < n; s++) {
        const cw_seg *g = &segs[s];
        const float dx = g->x1 - g->x0, dy = g->y1 - g->y0;
        const float len2 = dx * dx + dy * dy;
        const float inv = len2 > 1e-12f ? 1.0f / len2 : 0.0f;
        int xa = (int)floorf(fminf(g->x0, g->x1) - bezel), xb = (int)ceilf(fmaxf(g->x0, g->x1) + bezel);
        int ya = (int)floorf(fminf(g->y0, g->y1) - bezel), yb = (int)ceilf(fmaxf(g->y0, g->y1) + bezel);
        if (xa < 0) xa = 0;
        if (ya < 0) ya = 0;
        if (xb > w) xb = w;
        if (yb > h) yb = h;
        for (int y = ya; y < yb; y++) {
            const float cy = y + 0.5f;
            float *drow = dist + (size_t)y * w;
            for (int x = xa; x < xb; x++) {
                const float cx = x + 0.5f;
                float t = ((cx - g->x0) * dx + (cy - g->y0) * dy) * inv;
                t = clampf(t, 0.0f, 1.0f);
                const float qx = g->x0 + t * dx - cx, qy = g->y0 + t * dy - cy;
                const float d2 = qx * qx + qy * qy;
                if (d2 < b2 && d2 < drow[x]) {
                    drow[x] = d2;
                    const size_t i = (size_t)y * w + x;
                    nx[i] = g->n0x + (g->n1x - g->n0x) * t;
                    ny[i] = g->n0y + (g->n1y - g->n0y) * t;
                }
            }
        }
    }
    for (size_t i = 0; i < count; i++) {
        if (dist[i] < BIG) {
            dist[i] = sqrtf(dist[i]);
            const float l = sqrtf(nx[i] * nx[i] + ny[i] * ny[i]);
            if (l > 1e-6f) {
                nx[i] /= l;
                ny[i] /= l;
            }
        }
    }
}

/* ── backdrop preparation: box downsample + 3-pass box blur ─────────────────── */
static void box_blur_1d(float *data, float *tmp, int n, int stride, int r) {
    /* running-sum box blur of one line with clamped edges; 3 channels interleaved */
    if (r <= 0 || n <= 1) return;
    const float norm = 1.0f / (2 * r + 1);
    for (int c = 0; c < 3; c++) {
        float acc = 0.0f;
        for (int k = -r; k <= r; k++) {
            int idx = k < 0 ? 0 : k >= n ? n - 1 : k;
            acc += data[(size_t)idx * stride + c];
        }
        for (int i = 0; i < n; i++) {
            tmp[i] = acc * norm;
            int add = i + r + 1, sub = i - r;
            add = add >= n ? n - 1 : add;
            sub = sub < 0 ? 0 : sub;
            acc += data[(size_t)add * stride + c] - data[(size_t)sub * stride + c];
        }
        for (int i = 0; i < n; i++) data[(size_t)i * stride + c] = tmp[i];
    }
}

int cw_prepare_backdrop(const uint32_t *src, int sw, int sh, int sstride, int scale, int radius,
                        uint32_t *lo, int lw, int lh, int lstride) {
    float *buf = (float *)malloc(sizeof(float) * 3 * (size_t)lw * lh);
    float *tmp = (float *)malloc(sizeof(float) * (size_t)(lw > lh ? lw : lh));
    if (!buf || !tmp) {
        free(buf);
        free(tmp);
        return -1;
    }
    for (int y = 0; y < lh; y++) {
        for (int x = 0; x < lw; x++) {
            float r = 0, g = 0, b = 0;
            int cnt = 0;
            for (int yy = y * scale; yy < (y + 1) * scale && yy < sh; yy++) {
                const uint32_t *row = (const uint32_t *)((const uint8_t *)src + (size_t)yy * sstride);
                for (int xx = x * scale; xx < (x + 1) * scale && xx < sw; xx++) {
                    uint32_t p = row[xx];
                    r += (p >> 16) & 255;
                    g += (p >> 8) & 255;
                    b += p & 255;
                    cnt++;
                }
            }
            float *o = buf + 3 * ((size_t)y * lw + x);
            float inv = cnt ? 1.0f / cnt : 0.0f;
            o[0] = r * inv;
            o[1] = g * inv;
            o[2] = b * inv;
        }
    }
    for (int pass = 0; pass < 3; pass++) {
        for (int y = 0; y < lh; y++) box_blur_1d(buf + 3 * (size_t)y * lw, tmp, lw, 3, radius);
        for (int x = 0; x < lw; x++) box_blur_1d(buf + 3 * (size_t)x, tmp, lh, 3 * lw, radius);
    }
    for (int y = 0; y < lh; y++) {
        uint32_t *row = (uint32_t *)((uint8_t *)lo + (size_t)y * lstride);
        for (int x = 0; x < lw; x++) {
            const float *o = buf + 3 * ((size_t)y * lw + x);
            uint32_t r = (uint32_t)clampf(o[0] + 0.5f, 0, 255), g = (uint32_t)clampf(o[1] + 0.5f, 0, 255),
                     b = (uint32_t)clampf(o[2] + 0.5f, 0, 255);
            row[x] = 0xff000000u | (r << 16) | (g << 8) | b;
        }
    }
    free(buf);
    free(tmp);
    return 0;
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
   tint: do that once at low resolution and let cairo (pixman, SIMD) upsample it.
   `dst` has the same size and stride as p->lo. */
void cw_tint_lo(const cw_params *p, uint32_t *dst) {
    for (int y = 0; y < p->lo_h; y++) {
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

/* ── shading ────────────────────────────────────────────────────────────────── */
/* rows y_start, y_start + y_step, ... below y_end (interleaved rows balance threads) */
void cw_shade_rows(const cw_params *p, uint8_t *out, int y_start, int y_end, int y_step) {
    const float inv_bezel = p->bezel > 0.01f ? 1.0f / p->bezel : 0.0f;
    const float press_inv = p->press_r > 0.5f ? 1.0f / (p->press_r * p->press_r) : 0.0f;
    const float glow_inv = p->rim_glow > 0.01f ? 1.0f / p->rim_glow : 0.0f;
    const int skip = p->skip_interior && !(press_inv > 0.0f && p->press_a > 0.001f);
    if (y_step < 1) y_step = 1;
    for (int y = y_start; y < y_end; y += y_step) {
        uint32_t *orow = (uint32_t *)(out + (size_t)y * p->out_stride);
        const uint8_t *mrow = p->mask + (size_t)y * p->mask_stride;
        const float *drow = p->dist + (size_t)y * p->w;
        for (int x = 0; x < p->w; x++) {
            if (skip && mrow[x] == 255 && drow[x] >= p->bezel) continue;
            const uint32_t m = mrow[x];
            if (!m) {
                orow[x] = 0;
                continue;
            }
            const size_t i = (size_t)y * p->w + x;
            const float d = p->dist[i];
            const float cx = x + 0.5f, cy = y + 0.5f;

            float rgb[3];
            float spec = 0.0f, shade = 0.0f;
            if (d < p->bezel) {
                const float nxv = p->nx[i], nyv = p->ny[i];
                /* convex bezel: 0 offset where the flat top begins, `disp` at the rim,
                   with zero slope at both ends so there is no visible seam */
                const float u = 1.0f - d * inv_bezel;
                const float off = p->disp * u * u * (3.0f - 2.0f * u);
                if (p->dispersion > 0.001f && off > 0.05f) {
                    const float k = 0.18f * p->dispersion;
                    float t[3];
                    backdrop(p, cx - nxv * off * (1 + k), cy - nyv * off * (1 + k), t);
                    rgb[0] = t[0];
                    backdrop(p, cx - nxv * off, cy - nyv * off, t);
                    rgb[1] = t[1];
                    backdrop(p, cx - nxv * off * (1 - k), cy - nyv * off * (1 - k), t);
                    rgb[2] = t[2];
                } else {
                    backdrop(p, cx - nxv * off, cy - nyv * off, rgb);
                }
                /* two-sided specular: brightest where the edge faces the light,
                   a weaker reflection on the far side */
                const float facing = nxv * p->light_x + nyv * p->light_y;
                const float near_side = facing > 0 ? facing * sqrtf(facing) : 0.0f;
                const float far_side = facing < 0 ? -facing * sqrtf(-facing) : 0.0f;
                const float lit = p->rim_ambient + (1.0f - p->rim_ambient) * (near_side + p->rim_opposite * far_side);
                const float line = expf(-(d - 0.5f) * (d - 0.5f) * 1.6f);
                const float glow = expf(-d * glow_inv) * 0.35f;
                spec = p->rim * lit * (line + glow);
                shade = p->shade * expf(-d * inv_bezel * 3.0f) * (0.4f + 0.6f * far_side);
            } else {
                backdrop(p, cx, cy, rgb);
            }
            vibrancy(p, rgb);
            for (int c = 0; c < 3; c++) {
                float v = rgb[c] * (1.0f - shade);
                v = screen(v, clampf(spec, 0.0f, 1.0f));
                rgb[c] = v;
            }
            if (press_inv > 0.0f && p->press_a > 0.001f) {
                const float ddx = cx - p->press_x, ddy = cy - p->press_y;
                const float k = p->press_a * expf(-(ddx * ddx + ddy * ddy) * press_inv);
                for (int c = 0; c < 3; c++) rgb[c] = screen(rgb[c], k);
            }
            const float a = m * (1.0f / 255.0f);
            const uint32_t r = (uint32_t)(clampf(rgb[0], 0, 1) * a * 255.0f + 0.5f);
            const uint32_t g = (uint32_t)(clampf(rgb[1], 0, 1) * a * 255.0f + 0.5f);
            const uint32_t b = (uint32_t)(clampf(rgb[2], 0, 1) * a * 255.0f + 0.5f);
            orow[x] = (m << 24) | (r << 16) | (g << 8) | b;
        }
    }
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

int cw_abi_version(void) { return 4; }
