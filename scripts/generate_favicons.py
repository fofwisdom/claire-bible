"""Claire Bible - Magic Barrier Semi-Transparent Favicon Generator.

Recreates the glowing green magical barrier sphere (Slayers Claire Bible barrier)
with central light core and semi-transparent alpha channel in all standard web/PWA/mobile sizes.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance


def render_magic_sphere(size: int = 1024, is_small_optimized: bool = False) -> Image.Image:
    """Render the 3D magical barrier sphere at master resolution."""
    scale = 2
    W = size * scale
    H = size * scale
    cx = W / 2.0
    cy = H / 2.0
    R = W * 0.45  # Sphere radius in canvas pixels

    # Base transparent canvas
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # Camera distance for perspective projection
    cam_d = 4.0

    def project(pt3d: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = pt3d
        denom = cam_d - z
        factor = cam_d / denom
        px = cx + x * R * factor
        py = cy - y * R * factor
        return px, py, z

    # 1. Sphere Volume & Ambient Glow
    volume_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vol_draw = ImageDraw.Draw(volume_layer)

    # Soft radial gradient inside the sphere (ethereal cyan-green glow)
    for r_cur in range(int(R * 1.02), 0, -3):
        norm_r = r_cur / R
        if norm_r <= 1.0:
            # Ethereal translucent green-cyan interior volume
            alpha = int(22 * (1.0 - norm_r**2) + 16 * math.exp(-4 * ((norm_r - 0.9)**2)))
            vol_draw.ellipse(
                [cx - r_cur, cy - r_cur, cx + r_cur, cy + r_cur],
                fill=(0, 245, 175, alpha)
            )

    # Outer glowing limb / rim
    for rim_w in range(int(10 * scale), 0, -1):
        rim_a = int(60 * (1.0 - rim_w / (10 * scale)))
        vol_draw.ellipse(
            [cx - R, cy - R, cx + R, cy + R],
            outline=(0, 255, 180, rim_a),
            width=rim_w
        )

    # 2. 3D Seal Placement exactly matching the anime reference
    seal_configs = [
        # (nx, ny, nz, rot_deg, rad_mult)
        # 1. Upper-Front Seal (the large, iconic hexagram seal facing slightly up-forward)
        (0.0, 0.46, 0.89, 0, 0.47),
        # 2. Left-Front Seal (framing the left edge of the sphere)
        (-0.78, 0.10, 0.62, 18, 0.44),
        # 3. Right-Front Seal (framing the right edge of the sphere)
        (0.78, 0.10, 0.62, -18, 0.44),
        # 4. Bottom-Center Seal (lower front)
        (0.0, -0.68, 0.73, 30, 0.45),
        # 5. Bottom-Left Seal
        (-0.54, -0.52, 0.66, 12, 0.42),
        # 6. Bottom-Right Seal
        (0.54, -0.52, 0.66, -12, 0.42),
        # 7. Top-Back Seal
        (0.0, 0.78, -0.63, 0, 0.44),
        # 8. Left-Back Seal
        (-0.76, 0.28, -0.58, 40, 0.42),
        # 9. Right-Back Seal
        (0.76, 0.28, -0.58, -40, 0.42),
        # 10. Bottom-Back Seal
        (0.0, -0.76, -0.65, 0, 0.42),
        # 11. Bottom-Left-Back Seal
        (-0.58, -0.45, -0.68, 20, 0.40),
        # 12. Bottom-Right-Back Seal
        (0.58, -0.45, -0.68, -20, 0.40),
    ]

    normalized_seals = []
    for x, y, z, rot, rad_m in seal_configs:
        norm = math.sqrt(x*x + y*y + z*z)
        normalized_seals.append((x/norm, y/norm, z/norm, rot, rad_m))

    def get_basis(n: tuple[float, float, float]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        nx, ny, nz = n
        if abs(nx) < 0.9:
            ref = (1.0, 0.0, 0.0)
        else:
            ref = (0.0, 1.0, 0.0)
        ux = ref[1]*nz - ref[2]*ny
        uy = ref[2]*nx - ref[0]*nz
        uz = ref[0]*ny - ref[1]*nx
        ulen = math.sqrt(ux*ux + uy*uy + uz*uz)
        u = (ux/ulen, uy/ulen, uz/ulen)
        vx = ny*uz - nz*uy
        vy = nz*ux - nx*uz
        vz = nx*uy - ny*ux
        v = (vx, vy, vz)
        return u, v

    back_seals = [s for s in normalized_seals if s[2] < 0.1]
    front_seals = [s for s in normalized_seals if s[2] >= 0.1]

    def draw_seal_geometry(
        draw_ctx: ImageDraw.ImageDraw,
        core_draw_ctx: ImageDraw.ImageDraw,
        seal_info: tuple[float, float, float, float, float],
        is_front: bool
    ):
        nx, ny, nz, rot_deg, rad_mult = seal_info
        u, v = get_basis((nx, ny, nz))
        rot = math.radians(rot_deg)
        cos_r = math.cos(rot)
        sin_r = math.sin(rot)
        u_rot = (u[0]*cos_r + v[0]*sin_r, u[1]*cos_r + v[1]*sin_r, u[2]*cos_r + v[2]*sin_r)
        v_rot = (-u[0]*sin_r + v[0]*cos_r, -u[1]*sin_r + v[1]*cos_r, -u[2]*sin_r + v[2]*cos_r)

        z_factor = max(0.0, min(1.0, (nz + 0.8) / 1.8))
        
        if is_front:
            glow_w_out = max(2, int(6.5 * scale))
            glow_w_in = max(2, int(5.0 * scale))
            glow_w_star = max(2, int(4.5 * scale))

            core_w_out = max(1, int(3.2 * scale))
            core_w_in = max(1, int(2.4 * scale))
            core_w_star = max(1, int(2.2 * scale))

            glow_col = (0, 255, 160, int(230 + 25 * z_factor))
            mid_col = (40, 255, 190, int(240 + 15 * z_factor))
            white_col = (235, 255, 245, int(245 + 10 * z_factor))
        else:
            glow_w_out = max(1, int(3.2 * scale))
            glow_w_in = max(1, int(2.4 * scale))
            glow_w_star = max(1, int(2.0 * scale))

            core_w_out = max(1, int(1.6 * scale))
            core_w_in = max(1, int(1.2 * scale))
            core_w_star = max(1, int(1.1 * scale))

            glow_col = (0, 230, 150, int(80 + 70 * z_factor))
            mid_col = (30, 245, 175, int(100 + 70 * z_factor))
            white_col = (200, 255, 235, int(130 + 70 * z_factor))

        r_outer = 0.52 * rad_mult
        r_inner = 0.44 * rad_mult
        r_star = 0.43 * rad_mult

        # 1. Outer circle
        num_pts = 72
        pts_outer = []
        for i in range(num_pts + 1):
            theta = 2 * math.pi * i / num_pts
            px = nx + r_outer * (math.cos(theta) * u_rot[0] + math.sin(theta) * v_rot[0])
            py = ny + r_outer * (math.cos(theta) * u_rot[1] + math.sin(theta) * v_rot[1])
            pz = nz + r_outer * (math.cos(theta) * u_rot[2] + math.sin(theta) * v_rot[2])
            plen = math.sqrt(px*px + py*py + pz*pz)
            x_2d, y_2d, _ = project((px/plen, py/plen, pz/plen))
            pts_outer.append((x_2d, y_2d))
        draw_ctx.line(pts_outer, fill=glow_col, width=glow_w_out)
        core_draw_ctx.line(pts_outer, fill=white_col, width=core_w_out)

        # 2. Inner circle
        pts_inner = []
        for i in range(num_pts + 1):
            theta = 2 * math.pi * i / num_pts
            px = nx + r_inner * (math.cos(theta) * u_rot[0] + math.sin(theta) * v_rot[0])
            py = ny + r_inner * (math.cos(theta) * u_rot[1] + math.sin(theta) * v_rot[1])
            pz = nz + r_inner * (math.cos(theta) * u_rot[2] + math.sin(theta) * v_rot[2])
            plen = math.sqrt(px*px + py*py + pz*pz)
            x_2d, y_2d, _ = project((px/plen, py/plen, pz/plen))
            pts_inner.append((x_2d, y_2d))
        draw_ctx.line(pts_inner, fill=mid_col, width=glow_w_in)
        core_draw_ctx.line(pts_inner, fill=white_col, width=core_w_in)

        # 3. Hexagram (Two interlaced triangles)
        for offset_deg in [0, 60]:
            tri_pts = []
            for k in range(4):
                theta = math.radians(offset_deg + k * 120)
                px = nx + r_star * (math.cos(theta) * u_rot[0] + math.sin(theta) * v_rot[0])
                py = ny + r_star * (math.cos(theta) * u_rot[1] + math.sin(theta) * v_rot[1])
                pz = nz + r_star * (math.cos(theta) * u_rot[2] + math.sin(theta) * v_rot[2])
                plen = math.sqrt(px*px + py*py + pz*pz)
                x_2d, y_2d, _ = project((px/plen, py/plen, pz/plen))
                tri_pts.append((x_2d, y_2d))
            draw_ctx.line(tri_pts, fill=mid_col, width=glow_w_star)
            core_draw_ctx.line(tri_pts, fill=white_col, width=core_w_star)

    # 3. Connecting lattice mesh / energy arcs
    def draw_connecting_mesh(draw_ctx: ImageDraw.ImageDraw, is_front: bool):
        # Geodesic lines connecting seal centers
        front_centers = [s for s in normalized_seals if s[2] >= 0.0]
        for i in range(len(front_centers)):
            for j in range(i + 1, len(front_centers)):
                c1 = front_centers[i]
                c2 = front_centers[j]
                # Distance on sphere
                dot = c1[0]*c2[0] + c1[1]*c2[1] + c1[2]*c2[2]
                if 0.2 < dot < 0.9:  # Neighboring seals
                    pts = []
                    for step in range(11):
                        t = step / 10.0
                        # Slerp
                        theta = math.acos(max(-1.0, min(1.0, dot)))
                        sin_th = math.sin(theta)
                        if sin_th > 1e-4:
                            a = math.sin((1 - t) * theta) / sin_th
                            b = math.sin(t * theta) / sin_th
                            gx = a * c1[0] + b * c2[0]
                            gy = a * c1[1] + b * c2[1]
                            gz = a * c1[2] + b * c2[2]
                            if (is_front and gz >= -0.05) or (not is_front and gz < -0.05):
                                pts.append(project((gx, gy, gz))[:2])
                    if len(pts) > 1:
                        alpha = 90 if is_front else 35
                        draw_ctx.line(pts, fill=(0, 255, 175, alpha), width=max(1, int(1.4 * scale)))

        # Longitudinal & latitudinal arcs
        num_arcs = 12
        for arc_i in range(num_arcs):
            lon = 2 * math.pi * arc_i / num_arcs
            pts = []
            for lat_i in range(-16, 17):
                lat = (math.pi / 2) * (lat_i / 16.0)
                px = math.cos(lat) * math.sin(lon)
                py = math.sin(lat)
                pz = math.cos(lat) * math.cos(lon)
                if (is_front and pz >= -0.05) or (not is_front and pz < -0.05):
                    x_2d, y_2d, _ = project((px, py, pz))
                    pts.append((x_2d, y_2d))
                else:
                    if len(pts) > 1:
                        alpha = 100 if is_front else 40
                        w = max(1, int(1.5 * scale if is_front else 1.0 * scale))
                        draw_ctx.line(pts, fill=(0, 255, 175, alpha), width=w)
                    pts = []
            if len(pts) > 1:
                alpha = 100 if is_front else 40
                w = max(1, int(1.5 * scale if is_front else 1.0 * scale))
                draw_ctx.line(pts, fill=(0, 255, 175, alpha), width=w)

    # Prepare Layers
    back_glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    back_core_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    back_g_draw = ImageDraw.Draw(back_glow_layer)
    back_c_draw = ImageDraw.Draw(back_core_layer)

    front_glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    front_core_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    front_g_draw = ImageDraw.Draw(front_glow_layer)
    front_c_draw = ImageDraw.Draw(front_core_layer)

    center_light_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    center_light_draw = ImageDraw.Draw(center_light_layer)

    # Render Back
    draw_connecting_mesh(back_g_draw, is_front=False)
    for s in back_seals:
        draw_seal_geometry(back_g_draw, back_c_draw, s, is_front=False)

    # Render Central Light Core (Brilliant blue-white core light slightly below center)
    core_x, core_y, _ = project((0.0, -0.04, 0.0))
    
    # Soft radiant azure/cyan bloom
    for r in range(int(R * 0.46), 0, -2):
        frac = r / (R * 0.46)
        a = int(170 * ((1.0 - frac) ** 1.5))
        r_c = int(50 + 170 * (1.0 - frac))
        g_c = int(180 + 75 * (1.0 - frac))
        b_c = 255
        center_light_draw.ellipse(
            [core_x - r, core_y - r * 1.15, core_x + r, core_y + r * 1.15],
            fill=(r_c, g_c, b_c, a)
        )

    # Vertical light pillar / teardrop flare streak
    flare_h = R * 0.60
    flare_w = R * 0.08
    for fw in range(int(flare_w), 0, -1):
        fa = int(240 * (1.0 - fw / flare_w))
        center_light_draw.ellipse(
            [core_x - fw, core_y - flare_h * 0.8, core_x + fw, core_y + flare_h],
            fill=(215, 250, 255, fa)
        )

    # Diamond / starburst rays from core
    ray_len = R * 0.30
    for angle in [0, 45, 90, 135]:
        rad = math.radians(angle)
        dx = math.cos(rad) * ray_len
        dy = math.sin(rad) * ray_len
        center_light_draw.line(
            [(core_x - dx, core_y - dy), (core_x + dx, core_y + dy)],
            fill=(230, 255, 255, 180),
            width=max(1, int(1.6 * scale))
        )

    # Intense white-blue core orb
    for r in range(int(R * 0.12), 0, -1):
        frac = r / (R * 0.12)
        a = int(255 * (1.0 - frac * 0.4))
        center_light_draw.ellipse(
            [core_x - r, core_y - r, core_x + r, core_y + r],
            fill=(255, 255, 255, a)
        )

    # Render Front
    draw_connecting_mesh(front_g_draw, is_front=True)
    for s in front_seals:
        draw_seal_geometry(front_g_draw, front_c_draw, s, is_front=True)

    # Blur filters for neon bloom
    back_glow_blurred = back_glow_layer.filter(ImageFilter.GaussianBlur(radius=7 * scale))
    front_glow_wide = front_glow_layer.filter(ImageFilter.GaussianBlur(radius=10 * scale))
    front_glow_tight = front_glow_layer.filter(ImageFilter.GaussianBlur(radius=3.5 * scale))
    center_glow = center_light_layer.filter(ImageFilter.GaussianBlur(radius=8 * scale))

    # Composite layers
    img = Image.alpha_composite(img, volume_layer)
    img = Image.alpha_composite(img, back_glow_blurred)
    img = Image.alpha_composite(img, back_glow_layer)
    img = Image.alpha_composite(img, back_core_layer)
    img = Image.alpha_composite(img, center_glow)
    img = Image.alpha_composite(img, center_light_layer)
    img = Image.alpha_composite(img, front_glow_wide)
    img = Image.alpha_composite(img, front_glow_tight)
    img = Image.alpha_composite(img, front_glow_layer)
    img = Image.alpha_composite(img, front_core_layer)

    if size < 64:
        # Small favicon clarity optimization
        res = img.resize((size, size), Image.Resampling.LANCZOS)
        enhancer = ImageEnhance.Sharpness(res)
        res = enhancer.enhance(1.4)
        enhancer_c = ImageEnhance.Contrast(res)
        res = enhancer_c.enhance(1.15)
        return res
    else:
        return img.resize((size, size), Image.Resampling.LANCZOS)


def generate_svg_icon() -> str:
    """Generate modern SVG representation of the Claire Bible barrier favicon."""
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <defs>
    <!-- Glow filters -->
    <filter id="neon-glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="5" result="blur1"/>
      <feGaussianBlur stdDeviation="14" result="blur2"/>
      <feMerge>
        <feMergeNode in="blur2"/>
        <feMergeNode in="blur1"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
    
    <filter id="core-glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="12" result="coreBlur"/>
      <feMerge>
        <feMergeNode in="coreBlur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>

    <radialGradient id="sphere-vol" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#00f5b8" stop-opacity="0.16"/>
      <stop offset="75%" stop-color="#00f0a0" stop-opacity="0.08"/>
      <stop offset="96%" stop-color="#00ffb0" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="#00ffa0" stop-opacity="0.0"/>
    </radialGradient>

    <radialGradient id="core-flare" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="1.0"/>
      <stop offset="25%" stop-color="#d0f4ff" stop-opacity="0.95"/>
      <stop offset="60%" stop-color="#38b6ff" stop-opacity="0.6"/>
      <stop offset="100%" stop-color="#0080ff" stop-opacity="0.0"/>
    </radialGradient>

    <linearGradient id="flare-vert" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#c8f0ff" stop-opacity="0.0"/>
      <stop offset="50%" stop-color="#ffffff" stop-opacity="0.95"/>
      <stop offset="100%" stop-color="#c8f0ff" stop-opacity="0.0"/>
    </linearGradient>
  </defs>

  <!-- 1. Sphere Volume & Subtle Outer Aura -->
  <circle cx="256" cy="256" r="228" fill="url(#sphere-vol)" />
  <circle cx="256" cy="256" r="228" fill="none" stroke="#00ffaa" stroke-width="2.5" stroke-opacity="0.4" filter="url(#neon-glow)" />

  <!-- 2. Back Lattice Energy Lines -->
  <g fill="none" stroke="#00ff9d" stroke-opacity="0.32" stroke-width="1.8">
    <ellipse cx="256" cy="256" rx="228" ry="75" />
    <ellipse cx="256" cy="256" rx="75" ry="228" />
    <ellipse cx="256" cy="256" rx="162" ry="228" transform="rotate(45 256 256)" />
    <ellipse cx="256" cy="256" rx="162" ry="228" transform="rotate(-45 256 256)" />
  </g>

  <!-- 3. Back Magic Seals (Translucent) -->
  <g fill="none" stroke="#00e690" stroke-opacity="0.45" stroke-width="2.5">
    <ellipse cx="256" cy="95" rx="88" ry="36" />
    <polygon points="256,62 294,112 218,112" />
    <polygon points="256,128 294,78 218,78" />
    <ellipse cx="108" cy="210" rx="40" ry="88" transform="rotate(-20 108 210)" />
    <ellipse cx="404" cy="210" rx="40" ry="88" transform="rotate(20 404 210)" />
  </g>

  <!-- 4. Central Core Light Flare -->
  <g filter="url(#core-glow)">
    <ellipse cx="256" cy="265" rx="82" ry="96" fill="url(#core-flare)" />
    <ellipse cx="256" cy="265" rx="14" ry="125" fill="url(#flare-vert)" />
    <circle cx="256" cy="265" r="22" fill="#ffffff" />
  </g>

  <!-- 5. Front Magic Seals (Vibrant Glowing Neon Green) -->
  <g filter="url(#neon-glow)" fill="none" stroke="#26ffaa" stroke-linecap="round" stroke-linejoin="round">
    <!-- UPPER-FRONT SEAL (Large central-top hexagram) -->
    <g stroke-width="4.5">
      <ellipse cx="256" cy="158" rx="124" ry="94" />
      <ellipse cx="256" cy="158" rx="106" ry="80" stroke-width="3" stroke="#a6ffe0" />
      <polygon points="256,80 348,197 164,197" stroke-width="3.5" stroke="#ffffff" />
      <polygon points="256,236 348,119 164,119" stroke-width="3.5" stroke="#ffffff" />
    </g>

    <!-- LEFT SEAL -->
    <g stroke-width="4.5" transform="rotate(-22 135 250)">
      <ellipse cx="135" cy="250" rx="55" ry="122" />
      <ellipse cx="135" cy="250" rx="47" ry="105" stroke-width="3" stroke="#a6ffe0" />
      <polygon points="135,148 176,302 94,302" stroke-width="3.5" stroke="#ffffff" />
      <polygon points="135,352 176,198 94,198" stroke-width="3.5" stroke="#ffffff" />
    </g>

    <!-- RIGHT SEAL -->
    <g stroke-width="4.5" transform="rotate(22 377 250)">
      <ellipse cx="377" cy="250" rx="55" ry="122" />
      <ellipse cx="377" cy="250" rx="47" ry="105" stroke-width="3" stroke="#a6ffe0" />
      <polygon points="377,148 418,302 336,302" stroke-width="3.5" stroke="#ffffff" />
      <polygon points="377,352 418,198 336,198" stroke-width="3.5" stroke="#ffffff" />
    </g>

    <!-- BOTTOM-FRONT SEAL -->
    <g stroke-width="4.5">
      <ellipse cx="256" cy="390" rx="122" ry="58" />
      <ellipse cx="256" cy="390" rx="104" ry="50" stroke-width="3" stroke="#a6ffe0" />
      <polygon points="256,342 346,414 166,414" stroke-width="3.5" stroke="#ffffff" />
      <polygon points="256,438 346,366 166,366" stroke-width="3.5" stroke="#ffffff" />
    </g>
  </g>

  <!-- 6. Ultra-bright white core line highlights -->
  <g fill="none" stroke="#ffffff" stroke-width="1.8" stroke-opacity="0.9">
    <ellipse cx="256" cy="158" rx="106" ry="80" />
    <ellipse cx="256" cy="390" rx="104" ry="50" />
  </g>
</svg>
"""


def generate_all_icons(output_dir: Path):
    """Generate the full set of Mastodon-compatible favicons, touch icons, and webmanifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Generate master images
    print("Rendering master 1024x1024 image...")
    master_1024 = render_magic_sphere(1024)
    master_1024.save(output_dir / "apple-touch-icon-1024x1024.png", format="PNG", optimize=True)

    # 2. List of standard sizes from Mastodon repository
    android_sizes = [36, 48, 72, 96, 144, 192, 256, 384, 512]
    apple_sizes = [57, 60, 72, 76, 114, 120, 144, 152, 167, 180]
    favicon_sizes = [16, 32, 48]
    mstile_sizes = [(70, 70), (144, 144), (150, 150), (310, 150), (310, 310)]

    # Android Chrome icons
    for sz in android_sizes:
        print(f"Generating android-chrome-{sz}x{sz}.png...")
        img = render_magic_sphere(sz, is_small_optimized=(sz <= 48))
        img.save(output_dir / f"android-chrome-{sz}x{sz}.png", format="PNG", optimize=True)

    # Apple Touch icons
    for sz in apple_sizes:
        print(f"Generating apple-touch-icon-{sz}x{sz}.png...")
        img = render_magic_sphere(sz)
        img.save(output_dir / f"apple-touch-icon-{sz}x{sz}.png", format="PNG", optimize=True)

    # Default apple-touch-icon.png (180x180)
    touch_default = render_magic_sphere(180)
    touch_default.save(output_dir / "apple-touch-icon.png", format="PNG", optimize=True)

    # Favicon PNGs
    ico_images = []
    for sz in favicon_sizes:
        print(f"Generating favicon-{sz}x{sz}.png...")
        img = render_magic_sphere(sz, is_small_optimized=True)
        img.save(output_dir / f"favicon-{sz}x{sz}.png", format="PNG", optimize=True)
        ico_images.append(img)

    # Generate multi-resolution favicon.ico (16, 32, 48)
    print("Generating multi-resolution favicon.ico...")
    ico_images[0].save(
        output_dir / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=ico_images[1:]
    )

    # MS Tile icons
    for w, h in mstile_sizes:
        print(f"Generating mstile-{w}x{h}.png...")
        if w == h:
            img = render_magic_sphere(w)
        else:
            # Wide tile (310x150)
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            sq = render_magic_sphere(h)
            img.paste(sq, ((w - h) // 2, 0), sq)
        img.save(output_dir / f"mstile-{w}x{h}.png", format="PNG", optimize=True)

    # Generate SVG icons
    print("Generating favicon.svg...")
    svg_code = generate_svg_icon()
    (output_dir / "favicon.svg").write_text(svg_code, encoding="utf-8")
    (output_dir / "safari-pinned-tab.svg").write_text(svg_code, encoding="utf-8")

    # Generate manifest.json / site.webmanifest
    manifest_content = """{
  "name": "Claire Bible",
  "short_name": "Claire Bible",
  "description": "Personal Knowledge Base and Ontology Graph",
  "icons": [
    {
      "src": "/icon?p=android-chrome-36x36.png",
      "sizes": "36x36",
      "type": "image/png",
      "density": "0.75"
    },
    {
      "src": "/icon?p=android-chrome-48x48.png",
      "sizes": "48x48",
      "type": "image/png",
      "density": "1.0"
    },
    {
      "src": "/icon?p=android-chrome-72x72.png",
      "sizes": "72x72",
      "type": "image/png",
      "density": "1.5"
    },
    {
      "src": "/icon?p=android-chrome-96x96.png",
      "sizes": "96x96",
      "type": "image/png",
      "density": "2.0"
    },
    {
      "src": "/icon?p=android-chrome-144x144.png",
      "sizes": "144x144",
      "type": "image/png",
      "density": "3.0"
    },
    {
      "src": "/icon?p=android-chrome-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "density": "4.0",
      "purpose": "any maskable"
    },
    {
      "src": "/icon?p=android-chrome-256x256.png",
      "sizes": "256x256",
      "type": "image/png"
    },
    {
      "src": "/icon?p=android-chrome-384x384.png",
      "sizes": "384x384",
      "type": "image/png"
    },
    {
      "src": "/icon?p=android-chrome-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    }
  ],
  "theme_color": "#0e1116",
  "background_color": "#0e1116",
  "display": "standalone",
  "orientation": "any",
  "start_url": "/"
}
"""
    (output_dir / "manifest.json").write_text(manifest_content, encoding="utf-8")
    (output_dir / "site.webmanifest").write_text(manifest_content, encoding="utf-8")

    # Generate browserconfig.xml
    browserconfig_content = """<?xml version="1.0" encoding="utf-8"?>
<browserconfig>
    <msapplication>
        <tile>
            <square70x70logo src="/icon?p=mstile-70x70.png"/>
            <square150x150logo src="/icon?p=mstile-150x150.png"/>
            <wide310x150logo src="/icon?p=mstile-310x150.png"/>
            <square310x310logo src="/icon?p=mstile-310x310.png"/>
            <TileColor="#0e1116"/>
        </tile>
    </msapplication>
</browserconfig>
"""
    (output_dir / "browserconfig.xml").write_text(browserconfig_content, encoding="utf-8")
    print("All favicon assets generated successfully!")


def main():
    pkg_target_dir = Path("/home/fow/projects/claire-bible/src/claire/static/icons")
    root_target_dir = Path("/home/fow/projects/claire-bible/icons")
    generate_all_icons(pkg_target_dir)
    generate_all_icons(root_target_dir)


if __name__ == "__main__":
    main()
