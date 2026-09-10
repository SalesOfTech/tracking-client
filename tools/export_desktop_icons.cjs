// Export existing Lucide artwork; no handwritten icon paths.
const fs=require('node:fs'), path=require('node:path');
const dependencies=path.resolve(__dirname,'../desktop/node_modules');
const React=require(path.join(dependencies,'react'));
const {renderToStaticMarkup}=require(path.join(dependencies,'react-dom/server'));
const icons=require(path.join(dependencies,'lucide-react'));
const output=path.resolve(__dirname,'../agent/agent_tracker/ui/quick/icons');
fs.mkdirSync(output,{recursive:true});
for(const name of ['Link','Globe','Settings','CircleHelp','CircleCheck','RefreshCw','ExternalLink','ClipboardPaste','FolderOpen','ChevronRight','Monitor','Eye','Activity']) {
  for(const [variant,color] of Object.entries({neutral:'#586575',blue:'#087ee8',green:'#208b65',white:'#ffffff'})) {
    fs.writeFileSync(path.join(output,name+'-'+variant+'.svg'),renderToStaticMarkup(React.createElement(icons[name],{size:24,color,strokeWidth:1.8})));
  }
}
fs.copyFileSync(path.join(dependencies,'lucide-react/LICENSE'),path.join(output,'LICENSE'));
