'use strict';

// Redocly 1.x calls the legacy CommonJS default export. Keep that call shape
// while executing only the fixed upstream implementation, without modification.
module.exports = require('minimatch-fixed').minimatch;
