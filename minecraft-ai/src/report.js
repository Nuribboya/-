import { config } from './config.js';
import { Journal } from './journal.js';

console.log(new Journal(config.dataDir).report());
