const { test } = require('node:test');
const assert = require('node:assert/strict');
const { resolve, verifyTag, optional, publish, assets } = require('./fork-release.cjs');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const sha = 'a'.repeat(40), other = 'b'.repeat(40);
function fixture({ tag = sha, annotated = false, event = 'workflow_dispatch', ref = 'refs/heads/main', error = null } = {}) {
  const outputs = {}, calls = [];
  const repo = { owner: 'test-fork', repo: 'NapCatQQ' };
  const check = args => { assert.equal(args.owner, repo.owner); assert.equal(args.repo, repo.repo); calls.push(args); };
  const github = { rest: { repos: {
    get: async args => { check(args); return { data: { default_branch: 'main' } }; },
    getReleaseByTag: async args => { check(args); throw { status: 404 }; },
  }, git: {
    getRef: async args => {
      check(args);
      if (args.ref === 'heads/main') return { data: { object: { type: 'commit', sha } } };
      if (error) throw error;
      if (tag === null) throw { status: 404 };
      return { data: { object: { type: annotated ? 'tag' : 'commit', sha: tag } } };
    },
    getTag: async args => { check(args); return { data: { object: { type: 'commit', sha: tag } } }; },
  } } };
  return { github, context: { repo, eventName: event, ref }, core: { setOutput: (k, v) => outputs[k] = v, info: () => {} }, outputs, calls };
}

test('resolver fixes one main snapshot, permits a first release, and never queries upstream', async () => {
  process.env.RELEASE_TAG = 'v1.0.0';
  process.env.GITHUB_WORKFLOW_REF = 'test-fork/NapCatQQ/.github/workflows/release-publish.yml@refs/heads/main';
  for (const tag of [null, sha]) {
    const f = fixture({ tag });
    await resolve(f);
    assert.deepEqual(f.outputs, { source_sha: sha, release_tag: 'v1.0.0' });
    assert.equal(f.calls.filter(c => c.ref === 'heads/main').length, 1);
  }
});
test('push requires matching tag including annotated tags', async () => {
  process.env.GITHUB_WORKFLOW_REF = 'test-fork/NapCatQQ/.github/workflows/release-publish.yml@refs/tags/v1.0.0';
  for (const annotated of [false, true]) {
    const f = fixture({ event: 'push', ref: 'refs/tags/v1.0.0', annotated });
    await resolve(f);
    assert.equal(f.outputs.source_sha, sha);
  }
  for (const tag of [null, other]) await assert.rejects(resolve(fixture({ event: 'push', ref: 'refs/tags/v1.0.0', tag })), /expected main snapshot/);
});
test('manual dispatch rejects wrong branch, missing input, and existing mismatched tag', async () => {
  process.env.GITHUB_WORKFLOW_REF = 'test-fork/NapCatQQ/.github/workflows/release-publish.yml@refs/heads/main';
  await assert.rejects(resolve(fixture({ ref: 'refs/heads/feature' })), /select main/);
  process.env.RELEASE_TAG = '';
  await assert.rejects(resolve(fixture()), /release_tag/);
  process.env.RELEASE_TAG = 'v1.0.0';
  await assert.rejects(resolve(fixture({ tag: other })), /expected main snapshot/);
});
test('API errors are not mistaken for missing tags or releases', async () => {
  for (const error of [{ status: 403 }, { status: 429 }, { status: 500 }, new Error('network')]) {
    await assert.rejects(resolve(fixture({ error })), e => e === error);
    await assert.rejects(optional(async () => { throw error; }), e => e === error);
  }
});
test('publish-time tag drift fails rather than overwriting', async () => {
  const f = fixture({ tag: other });
  await assert.rejects(verifyTag(f.github, f.context.repo, 'v1', sha, true), /expected main snapshot/);
});
test('concurrent creation rechecks commit; existing unrelated body/assets survive', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'fork-release-'));
  const previous = process.cwd();
  try {
    process.chdir(temp);
    fs.mkdirSync('release-assets');
    for (const name of assets) fs.writeFileSync(path.join('release-assets', name), Buffer.alloc(2048));
    process.env.SOURCE_SHA = sha;
    process.env.RELEASE_TAG = 'v1.0.0';
    for (const conflictSha of [sha, other]) {
      const f = fixture({ tag: null });
      let created = false, count = 0;
      f.github.rest.git.getRef = async () => {
        if (!created) throw { status: 404 };
        return { data: { object: { type: 'commit', sha: conflictSha } } };
      };
      f.github.rest.git.createRef = async args => { assert.equal(args.sha, sha); created = true; throw { status: 422 }; };
      f.github.rest.repos.getReleaseByTag = async () => ({ data: { id: 1, body: 'Unrelated user notes', html_url: 'https://example.test' } });
      f.github.rest.repos.updateRelease = async args => { assert.ok(args.body.startsWith('Unrelated user notes')); };
      f.github.paginate = async () => [{ name: 'unrelated.txt', id: 7 }];
      f.github.rest.repos.deleteReleaseAsset = async () => assert.fail('Must preserve unrelated asset');
      f.github.rest.repos.uploadReleaseAsset = async args => { assert.ok(assets.includes(args.name)); count++; };
      if (conflictSha === sha) { await publish(f); assert.equal(count, 4); }
      else { await assert.rejects(publish(f), /expected main snapshot/); assert.equal(count, 0); }
    }
  } finally {
    process.chdir(previous);
    fs.rmSync(temp, { recursive: true });
  }
});
test('every build/publish checkout uses resolver SHA and required jobs gate publication', () => {
  const yaml = fs.readFileSync(path.join(__dirname, '../workflows/release-publish.yml'), 'utf8');
  assert.equal((yaml.match(/ref: \$\{\{ needs.resolve-main.outputs.source_sha \}\}/g) || []).length, 3);
  assert.match(yaml, /needs: \[resolve-main, preflight, build\]/);
  assert.match(yaml, /fail-fast: false/);
  assert.doesNotMatch(yaml, /macos-15|platform: macOS-arm64/);
  assert.equal(assets.length, 4);
  assert.ok(assets.every(name => !name.includes('macOS')));
  assert.doesNotMatch(yaml, /secrets\.(NAPCAT_BUILD|APIFOX_TOKEN)|workflow_call|repository_dispatch/);
});
