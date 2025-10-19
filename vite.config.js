import { defineConfig } from 'vite'
import { resolve } from 'path'

export default defineConfig({
  // Build configuration
  build: {
    // Output directory for built assets
    outDir: 'vibe_coding/static/vibe_coding/js/dist',
    emptyOutDir: true,
    
    // Entry points for both JS and TS implementations
    rollupOptions: {
      input: {
        'pixel-war-app': resolve(__dirname, 'vibe_coding/static/vibe_coding/js/pixel_war/pixel-war-app.js'),
        'pixel-war-ts-app': resolve(__dirname, 'vibe_coding/static/vibe_coding/ts/pixel_war_ts/pixel-war-ts-app.ts')
      },
      output: {
        // Generate separate chunks for heavy dependencies (disabled as these packages aren't used)
        // manualChunks: {
        //   'pixi-core': ['pixi.js'],
        //   'pixi-viewport': ['pixi-viewport'],
        //   'mobile-helpers': ['hammerjs']
        // },
        // Ensure proper naming for Django static files
        entryFileNames: '[name].bundle.js',
        chunkFileNames: '[name]-[hash].chunk.js',
        assetFileNames: '[name]-[hash].[ext]'
      }
    },
    
    // Source maps for development debugging
    sourcemap: true,
    
    // Optimize for production
    minify: 'esbuild',
    target: 'es2020'
  },
  
  // Development server configuration
  server: {
    // Proxy to Django development server
    proxy: {
      // Forward all non-asset requests to Django
      '^(?!/node_modules|/@vite|/src).*': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    },
    cors: true,
    port: 3000
  },
  
  // Dependency optimization
  optimizeDeps: {
    // Currently no external dependencies used in pixel war
    // include: ['pixi.js', 'pixi-viewport', 'hammerjs'],
    force: true
  },
  
  // Plugin configuration
  plugins: [
    // Custom plugin to handle Django integration
    {
      name: 'django-integration',
      generateBundle(options, bundle) {
        // Generate a manifest file for Django to know about built files
        const manifest = {};
        
        for (const [fileName, chunk] of Object.entries(bundle)) {
          if (chunk.type === 'chunk' && chunk.isEntry) {
            // Map original entry names to built file names
            const originalName = chunk.name || chunk.facadeModuleId?.split('/').pop()?.replace('.js', '');
            manifest[originalName] = fileName;
          }
        }
        
        // Write manifest for Django static file integration
        this.emitFile({
          type: 'asset',
          fileName: 'manifest.json',
          source: JSON.stringify(manifest, null, 2)
        });
      }
    }
  ],
  
  // Base public path for assets
  base: '/static/vibe_coding/js/dist/',
  
  // Define globals for better tree-shaking
  define: {
    __DEV__: process.env.NODE_ENV !== 'production'
  }
})