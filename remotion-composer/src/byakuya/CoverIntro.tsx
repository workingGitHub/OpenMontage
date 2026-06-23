import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type Props = {
  coverSrc: string;
  bookTitle: string;
  author: string;
  subtitle?: string;
};

export const CoverIntro: React.FC<Props> = ({
  coverSrc,
  bookTitle,
  author,
  subtitle,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Background image: slow 5% zoom-in over the duration
  const zoom = interpolate(frame, [0, durationInFrames], [1.0, 1.08], {
    extrapolateRight: "clamp",
  });

  // Title: per-character spring, holds, then fades out near end
  const titleChars = bookTitle.split("");
  const titleSpring = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 80, mass: 0.8 },
  });
  const titleOpacity = interpolate(
    frame,
    [durationInFrames - 20, durationInFrames - 4],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  // Author: gentle fade-in after title settles
  const authorOpacity = interpolate(
    frame,
    [fps * 0.9, fps * 1.6],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {/* Background cover image with slow Ken Burns */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          transform: `scale(${zoom})`,
        }}
      >
        <Img
          src={staticFile(coverSrc)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      </div>

      {/* Dark vignette to deepen the cover */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(ellipse at center, rgba(0,0,0,0.0) 35%, rgba(0,0,0,0.55) 100%)",
        }}
      />

      {/* Title block — bottom-left */}
      <div
        style={{
          position: "absolute",
          left: 96,
          bottom: 110,
          maxWidth: "80%",
          opacity: titleOpacity,
          transform: `translateY(${(1 - titleSpring) * 24}px)`,
        }}
      >
        <div
          style={{
            fontFamily: "Playfair Display, Georgia, serif",
            fontSize: 110,
            fontWeight: 700,
            color: "#F5F7FA",
            letterSpacing: 4,
            lineHeight: 1,
            display: "flex",
            textShadow: "0 6px 32px rgba(0,0,0,0.65)",
          }}
        >
          {titleChars.map((c, i) => (
            <span key={i} style={{ marginRight: c === " " ? 16 : 4 }}>
              {c}
            </span>
          ))}
        </div>
        <div
          style={{
            marginTop: 18,
            fontFamily: "Space Grotesk, Inter, system-ui, sans-serif",
            fontSize: 28,
            fontWeight: 400,
            color: "rgba(245,247,250,0.78)",
            letterSpacing: 6,
          }}
        >
          {author}
        </div>
        {subtitle ? (
          <div
            style={{
              marginTop: 10,
              fontFamily: "Space Grotesk, Inter, system-ui, sans-serif",
              fontSize: 22,
              color: "rgba(245,247,250,0.55)",
              letterSpacing: 4,
              opacity: authorOpacity,
            }}
          >
            {subtitle}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
