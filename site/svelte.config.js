import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      strict: true
    }),
    prerender: {
      handleUnseenRoutes: ({ routes }) => {
        const dataDrivenRoutes = new Set(['/cases/[slug]', '/terms/[term]']);
        const unexpected = routes.filter((route) => !dataDrivenRoutes.has(route));
        if (unexpected.length)
          throw new Error(`Routes were not prerendered: ${unexpected.join(', ')}`);
      }
    },
    alias: {
      $data: './src/lib/server/data'
    }
  }
};

export default config;
