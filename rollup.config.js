import deckyPlugin from "@decky/rollup";

const config = deckyPlugin({});
const assets = config.plugins.find((plugin) => plugin.name === "assets");
const loadAsset = assets.load;

assets.load = function (id) {
  const code = loadAsset.call(this, id);
  // Decky's asset loader inserts the plugin name into a single-quoted string.
  return typeof code === "string"
    ? code.replace(/^export default '(.*)'$/, (_, url) => `export default ${JSON.stringify(url)}`)
    : code;
};

export default config;
