import { staticClasses } from "@decky/ui";
import { definePlugin } from "@decky/api";
import { GiPlasticDuck } from "react-icons/gi";
import manifest from "@decky/manifest";
import { Content } from "./components/Content";

export default definePlugin(() => {
  console.log("decky-lsfg-vk plugin initializing");

  return {
    name: manifest.name,
    titleView: <div className={staticClasses.Title}>{manifest.name}</div>,
    alwaysRender: true,
    content: <Content />,
    icon: <GiPlasticDuck />,
    onDismount() {
      console.log("decky-lsfg-vk unloading");
    }
  };
});
