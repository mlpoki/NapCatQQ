const fs = require('node:fs');
const path = require('node:path');

const assets = [
  'NapCat.Shell.Windows.OneKey.zip', 'NapCat-QCE-Windows-x64.zip',
  'NapCat-Framework-QCE.zip', 'NapCat-QCE-Linux-x64.tar.gz',
  'NapCat-QCE-macOS-arm64.tar.gz',
];
async function optional(request) {
  try { return (await request()).data; } catch (e) {
    if (e.status === 404) return null;
    throw e;
  }
}
async function tagCommit(github, repo, tag) {
  const ref = await optional(() => github.rest.git.getRef({ ...repo, ref: `tags/${tag}` }));
  if (!ref) return null;
  let object = ref.object;
  for (let i = 0; object.type === 'tag' && i < 10; i++) {
    object = (await github.rest.git.getTag({ ...repo, tag_sha: object.sha })).data.object;
  }
  if (object.type !== 'commit') throw new Error('Tag does not resolve to a commit');
  return object.sha;
}
async function verifyTag(github, repo, tag, sha, allowMissing) {
  const actual = await tagCommit(github, repo, tag);
  if (actual !== sha && !(actual === null && allowMissing)) {
    throw new Error(`Tag ${tag} points to ${actual}; expected main snapshot ${sha}`);
  }
  return actual;
}
async function resolve({ github, context, core }) {
  const repo = context.repo;
  if ((await github.rest.repos.get(repo)).data.default_branch !== 'main') throw new Error('Default branch must be main');
  if (context.eventName === 'workflow_dispatch' && context.ref !== 'refs/heads/main') throw new Error('Dispatch must select main');
  if (!['push', 'workflow_dispatch'].includes(context.eventName)) throw new Error('Unsupported event');
  const tag = context.eventName === 'push' ? context.ref.replace(/^refs\/tags\//, '') : process.env.RELEASE_TAG;
  if (!/^v[0-9A-Za-z][0-9A-Za-z._+-]*$/.test(tag || '')) throw new Error('release_tag must be a v* tag');
  const sha = (await github.rest.git.getRef({ ...repo, ref: 'heads/main' })).data.object.sha;
  await verifyTag(github, repo, tag, sha, context.eventName === 'workflow_dispatch');
  const release = await optional(() => github.rest.repos.getReleaseByTag({ ...repo, tag }));
  if (release && (release.draft || release.prerelease || release.immutable)) throw new Error('Release must be mutable and already formal');
  const workflowRef = process.env.GITHUB_WORKFLOW_REF || '';
  const expectedRef = context.eventName === 'push' ? context.ref : 'refs/heads/main';
  if (!workflowRef.endsWith(`@${expectedRef}`)) throw new Error('Unexpected workflow entry ref');
  core.setOutput('source_sha', sha);
  core.setOutput('release_tag', tag);
  core.info(JSON.stringify({ source_branch: 'main', source_sha: sha, release_tag: tag, workflow_sha: process.env.GITHUB_WORKFLOW_SHA }));
}
async function publish({ github, context, core }) {
  const repo = context.repo;
  const tag = process.env.RELEASE_TAG;
  const sha = process.env.SOURCE_SHA;
  const files = fs.readdirSync('release-assets').sort();
  if (JSON.stringify(files) !== JSON.stringify([...assets].sort())) throw new Error(`Unexpected assets: ${files}`);
  for (const name of assets) if (fs.statSync(path.join('release-assets', name)).size < 1024) throw new Error(`Empty asset: ${name}`);
  if (!(await verifyTag(github, repo, tag, sha, true))) {
    try { await github.rest.git.createRef({ ...repo, ref: `refs/tags/${tag}`, sha }); }
    catch (e) { if (e.status !== 422) throw e; }
  }
  await verifyTag(github, repo, tag, sha, false);
  let release = await optional(() => github.rest.repos.getReleaseByTag({ ...repo, tag }));
  if (release && (release.draft || release.prerelease || release.immutable)) throw new Error('Release cannot be modified');
  const start = '<!-- fork-release:start -->';
  const end = '<!-- fork-release:end -->';
  const report = `${start}\nsource_branch=main\nsource_sha=${sha}\nworkflow_sha=${process.env.GITHUB_WORKFLOW_SHA}\nrelease_tag=${tag}\nQCE=v6.4.3 (4317f8c69b30c60bae47b543817b227665fa4f30)\nBuild: ${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}\n\n${assets.map(n => `- ${n}`).join('\n')}\n\nQQ login, live chat export and desktop interaction: pending manual verification.\n${end}`;
  const previous = release?.body || '';
  const first = previous.indexOf(start), last = previous.indexOf(end);
  const body = first >= 0 && last >= first ? previous.slice(0, first) + report + previous.slice(last + end.length) : `${previous}\n\n${report}`.trim();
  if (!release) release = (await github.rest.repos.createRelease({ ...repo, tag_name: tag, target_commitish: sha, name: `NapCat ${tag}`, body, draft: false, prerelease: false })).data;
  else await github.rest.repos.updateRelease({ ...repo, release_id: release.id, body });
  const existing = await github.paginate(github.rest.repos.listReleaseAssets, { ...repo, release_id: release.id, per_page: 100 });
  for (const name of assets) {
    await verifyTag(github, repo, tag, sha, false);
    const old = existing.find(a => a.name === name);
    if (old) await github.rest.repos.deleteReleaseAsset({ ...repo, asset_id: old.id });
    await github.rest.repos.uploadReleaseAsset({ ...repo, release_id: release.id, name, data: fs.readFileSync(path.join('release-assets', name)) });
    core.info(`Uploaded ${name}; uploads are not transactional`);
  }
  await verifyTag(github, repo, tag, sha, false);
  core.info(release.html_url);
}
module.exports = { assets, optional, tagCommit, verifyTag, resolve, publish };
