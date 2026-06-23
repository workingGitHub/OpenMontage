import React from "react";
import { Composition, registerRoot } from "remotion";
import { CoverIntro } from "./byakuya/CoverIntro";

const ByakuyaIntroRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="ByakuyaCoverIntro"
        component={CoverIntro}
        durationInFrames={90}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          coverSrc: "cover.jpg",
          bookTitle: "白夜行",
          author: "东野圭吾  ·  Keigo Higashino",
          subtitle: "一九七三  —  一九九二",
        }}
      />
    </>
  );
};

registerRoot(ByakuyaIntroRoot);
